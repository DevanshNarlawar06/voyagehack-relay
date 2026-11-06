"""FastAPI app serving every mock system. Run: uvicorn mocks.app:create_app --factory --port 9000"""

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ValidationError

from mocks.faults import Fault, FaultController, FaultRule, FaultRuleIn
from mocks.store import Record, StoreResult, SystemStore
from mocks.systems import SYSTEMS, ActionSpec, SystemSpec

FAULT_HEADER = "X-Fault"
IDEMPOTENCY_HEADER = "Idempotency-Key"
REPLAY_HEADER = "Idempotent-Replayed"


@dataclass(frozen=True, slots=True)
class MockSettings:
    """Delays for the time-based faults. Env vars let the demo tune them; tests shrink them."""

    slow_seconds: float = float(os.environ.get("MOCK_SLOW_SECONDS", "3"))
    timeout_seconds: float = float(os.environ.get("MOCK_TIMEOUT_SECONDS", "30"))


def _to_response(result: StoreResult) -> JSONResponse:
    headers = {REPLAY_HEADER: "true"} if result.replayed else None
    return JSONResponse(result.body, status_code=result.status, headers=headers)


class _Runtime:
    """Applies the fault for a request around the op's actual work."""

    def __init__(self, settings: MockSettings, faults: FaultController) -> None:
        self.settings = settings
        self.faults = faults

    async def run(
        self,
        request: Request,
        store: SystemStore,
        system: str,
        op: str,
        work: Callable[[bool], Response],
    ) -> Response:
        """Run `work`, passing whether idempotency must be bypassed, under the resolved fault."""
        store.requests += 1
        try:
            fault = self.faults.resolve(system, op, request.headers.get(FAULT_HEADER))
        except ValueError:
            return JSONResponse({"error": "unknown_fault"}, status_code=400)
        if fault is Fault.SERVICE_UNAVAILABLE:
            return JSONResponse(
                {"error": "service_unavailable"}, status_code=503, headers={"Retry-After": "1"}
            )
        if fault is Fault.SLOW:
            await asyncio.sleep(self.settings.slow_seconds)
        if fault is Fault.TIMEOUT:
            await asyncio.sleep(self.settings.timeout_seconds)
            return JSONResponse({"error": "gateway_timeout"}, status_code=504)
        response = work(fault is Fault.DUPLICATE_ON_RETRY)
        if fault is Fault.TIMEOUT_AFTER_COMMIT:
            await asyncio.sleep(self.settings.timeout_seconds)
            return JSONResponse({"error": "gateway_timeout"}, status_code=504)
        return response


async def _parse(request: Request, model: type[BaseModel]) -> BaseModel | JSONResponse:
    """Validate the JSON body against `model`, returning a 422 response if it does not fit."""
    try:
        raw = await request.body()
        return model.model_validate_json(raw or b"{}")
    except ValidationError as exc:
        details = exc.errors(include_url=False, include_input=False, include_context=False)
        return JSONResponse({"error": "invalid_request", "details": details}, status_code=422)


def _add_action(
    router: APIRouter, spec: SystemSpec, store: SystemStore, runtime: _Runtime, action: ActionSpec
) -> None:
    async def run_action(record_id: str, request: Request) -> Response:
        parsed = await _parse(request, action.model)
        if isinstance(parsed, JSONResponse):
            return parsed
        payload = parsed.model_dump()
        append = None
        if action.append is not None:
            list_field, body_field = action.append
            append = (list_field, str(payload[body_field]))

        def work(bypass: bool) -> Response:
            return _to_response(
                store.act(
                    record_id,
                    status=action.status,
                    append=append,
                    payload=payload,
                    idempotency_key=request.headers.get(IDEMPOTENCY_HEADER),
                    bypass_idempotency=bypass,
                )
            )

        return await runtime.run(request, store, spec.name, action.op, work)

    router.add_api_route(
        f"/{spec.collection}/{{record_id}}/{action.path}",
        run_action,
        methods=["POST"],
        name=f"{spec.name}.{action.op}",
    )


def _system_router(spec: SystemSpec, store: SystemStore, runtime: _Runtime) -> APIRouter:
    router = APIRouter(prefix=f"/{spec.name}", tags=[spec.name])

    @router.get("/health", name=f"{spec.name}.health")
    async def health(request: Request) -> Response:
        return await runtime.run(
            request,
            store,
            spec.name,
            "health",
            lambda _bypass: JSONResponse({"status": "ok", "system": spec.name}),
        )

    @router.post(f"/{spec.collection}", name=f"{spec.name}.{spec.create_op}")
    async def create(request: Request) -> Response:
        parsed = await _parse(request, spec.create_model)
        if isinstance(parsed, JSONResponse):
            return parsed
        payload: Record = parsed.model_dump()

        def work(bypass: bool) -> Response:
            return _to_response(
                store.create(
                    payload,
                    idempotency_key=request.headers.get(IDEMPOTENCY_HEADER),
                    bypass_idempotency=bypass,
                )
            )

        return await runtime.run(request, store, spec.name, spec.create_op, work)

    @router.get(f"/{spec.collection}", name=f"{spec.name}.{spec.lookup_op}")
    async def lookup(request: Request) -> Response:
        def work(_bypass: bool) -> Response:
            items = store.lookup(dict(request.query_params))
            return JSONResponse({"items": items, "count": len(items)})

        return await runtime.run(request, store, spec.name, spec.lookup_op, work)

    @router.get(f"/{spec.collection}/{{record_id}}", name=f"{spec.name}.{spec.read_op}")
    async def read(record_id: str, request: Request) -> Response:
        def work(_bypass: bool) -> Response:
            record = store.records.get(record_id)
            if record is None:
                return JSONResponse({"error": "not_found"}, status_code=404)
            return JSONResponse(record)

        return await runtime.run(request, store, spec.name, spec.read_op, work)

    for action in spec.actions:
        _add_action(router, spec, store, runtime, action)
    return router


def _control_router(
    specs: tuple[SystemSpec, ...], stores: dict[str, SystemStore], faults: FaultController
) -> APIRouter:
    router = APIRouter(prefix="/_control", tags=["control"])
    by_name = {spec.name: spec for spec in specs}

    @router.get("/faults")
    async def list_faults() -> list[FaultRule]:
        return faults.list()

    @router.post("/faults", status_code=201, response_model=None)
    async def add_fault(rule: FaultRuleIn) -> FaultRule | JSONResponse:
        if rule.system != "*" and rule.system not in by_name:
            return JSONResponse({"error": "unknown_system"}, status_code=422)
        known_ops = (
            set().union(*(s.ops for s in specs)) if rule.system == "*" else by_name[rule.system].ops
        )
        if rule.op != "*" and rule.op not in known_ops:
            return JSONResponse({"error": "unknown_op"}, status_code=422)
        return faults.add(rule)

    @router.delete("/faults/{rule_id}", status_code=204, response_model=None)
    async def remove_fault(rule_id: str) -> Response:
        code = 204 if faults.remove(rule_id) else 404
        return Response(status_code=code)

    @router.delete("/faults", status_code=204)
    async def clear_faults() -> None:
        faults.clear()

    @router.get("/stats")
    async def stats() -> dict[str, dict[str, int]]:
        return {
            name: {
                "records": len(store.records),
                "requests": store.requests,
                "side_effects": store.side_effects,
                "replays": store.replays,
            }
            for name, store in stores.items()
        }

    @router.post("/reset", status_code=204)
    async def reset() -> None:
        faults.clear()
        for store in stores.values():
            store.reset()

    return router


def create_app(settings: MockSettings | None = None) -> FastAPI:
    """Build the mock service with fresh state: one router per system plus `/_control`."""
    faults = FaultController()
    runtime = _Runtime(settings or MockSettings(), faults)
    stores = {
        spec.name: SystemStore(spec.id_prefix, spec.mode, spec.natural_key) for spec in SYSTEMS
    }
    app = FastAPI(title="Relay mock systems", version="1")

    @app.get("/healthz", tags=["control"])
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(_control_router(SYSTEMS, stores, faults))
    for spec in SYSTEMS:
        app.include_router(_system_router(spec, stores[spec.name], runtime))
    return app
