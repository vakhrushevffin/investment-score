# Investment Score 2.0 — реестр до 1000 акций

Исходный рейтинг сохранён из загруженного пользователем репозитория. Добавлена страница технических светофоров, реестр `data/tickers.csv`, пакетные загрузчики и GitHub Actions. См. `DEPLOY.md`.

Команды:

```bash
pip install -r requirements.txt
python -m scripts.update_data --mode all --dry-run
streamlit run app.py
```

Обновление цен: `python -m scripts.update_data --mode prices --batch-size 40`. SEC: задайте `SEC_CONTACT_EMAIL` и запустите `--mode fundamentals`.
