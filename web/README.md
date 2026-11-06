# web/ (HR console)

Next.js 15 (App Router), TypeScript, Tailwind, TanStack Query and Recharts (PRD 16.3). Owner: Session B, WP10. The UI primitives in `components/ui.tsx` follow shadcn/ui's patterns, hand-written with one accent colour.

```bash
npm install
npm run dev
```

**Data source.** With `NEXT_PUBLIC_RELAY_API_URL` unset, the console serves the invented data in `lib/fixtures.ts` and every screen shows a "Demo data" banner. Set it to the API's origin (for example `http://localhost:8000`) to use real data, and the banner goes away. The contract is `RelayApi` in `lib/types.ts`.

**Tests.** The Playwright smoke test walks login → dashboard → resolve a task → activate a policy → kill switch, against the fixtures:

```bash
npx playwright install chromium
npx playwright test
```
