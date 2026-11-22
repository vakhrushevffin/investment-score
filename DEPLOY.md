# Deployment / публикация

1. Make a backup branch or fork. Upload **contents** of this ZIP to repository root, preserving `.github`, `.streamlit`, `pages`, `scripts`, `data`, `engine`.
2. Streamlit entry point remains `app.py`. Confirm that the main ranking and `🚦 Технические светофоры` page both load.
3. GitHub Actions → Update daily prices → Run workflow. Confirm the workflow completes and commits refreshed archive; the bundled archive is **historical** (20 Nov 2026).
4. For SEC fundamentals, create repository Actions secret `SEC_CONTACT_EMAIL` (your real contact email) and run **Update SEC fundamentals** manually. Review `data/refresh_log.csv`, especially incomplete/misaligned filings. Never commit credentials.
5. Add rows to `data/tickers.csv` (ticker, SEC CIK, enabled, fundamental_enabled, technical_enabled). Existing data remains unchanged until workflows complete. For companies without a verified SEC CIK set `fundamental_enabled=false`; they can still appear in traffic lights.
6. To expand to 1000: test 28 → 100 → 500 → 1000. Check data source rate limits, legal licensing for commercial redistribution, runtime, GitHub Actions quotas and repository growth. GitHub Actions schedules may be delayed or disabled on inactive public repositories.

**Caveats:** no database; updates commit snapshots to git, not persistent mutable user portfolios. Stock registry does not auto-discover CIKs. Historical prices are third-party, and the model is research-only. Full end-to-end cloud deployment and live API calls have not been tested here.
