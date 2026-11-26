# Investment Score v2.4.0 — установка

1. Распакуйте `Investment_Score_v2_4_0.zip`. Откройте рабочую копию GitHub и переключитесь на `investment-score-v2`. Сохраните текущий commit ветки для отката. Перенесите содержимое ZIP в корень рабочей копии вместе со скрытыми папками `.github` и `.streamlit`.
2. Если в ветке уже есть архив котировок новее 25 ноября 2026 года, сохраните его. Вместе с новым реестром примените процедуру добавления истории ниже к более свежему архиву. Аналогично сохраните более свежие SEC-строки исходной выборки при слиянии снимков.
3. В Python 3.11 или 3.12 выполните команды из корня проекта.

   ```bash
   python -m pip install -r requirements.txt
   python -m unittest discover -s tests -v
   python -m streamlit run app.py
   ```

4. Проверьте Fundamental Analysis и Technical Risk Monitor. Найдите MU, HPE и DELL. Сверьте дату котировок и статусы SEC. SPY используется как бенчмарк отдельно от рейтинга.
5. Проверьте список изменений через `git status`. Закоммитьте файлы и отправьте ветку командой `git push origin HEAD:refs/heads/investment-score-v2`.
6. В Render для тестового сервиса выберите ветку `investment-score-v2`. Команда сборки — `pip install -r requirements.txt`. Команда запуска — `streamlit run app.py --server.address 0.0.0.0 --server.port "$PORT" --server.headless true`. После развёртывания откройте обе страницы и проверьте дату архива.

## Обновление данных

- В GitHub Actions запустите **Safe Price Update (Dry Run)** для ветки `investment-score-v2`. Проверьте `summary.json` и `errors.csv` в артефакте. Затем запустите **Automatic Price Update (Test Branch)**. Публикация возможна после повторной сверки с Yahoo Finance, проверки торговых сессий и хеша базы. При отсутствии новых сессий workflow может успешно завершиться без коммита. В переданном исходнике запуск обоих workflows выполнялся вручную через `workflow_dispatch`.
- Для **Update SEC fundamentals** задайте в секретах GitHub Actions `SEC_CONTACT_EMAIL` с рабочим контактным адресом. Запустите workflow на `investment-score-v2`. В `data/refresh_log.csv` смотрите результаты по каждой метрике. При ошибке команды workflow останавливается до коммита.
- Проверка планов пакетной загрузки локально.

   ```bash
   python -m scripts.update_data --mode fundamentals --batch-size 20 --dry-run
   python -m scripts.update_data --mode prices --batch-size 40 --dry-run
   ```

## Добавление истории новым тикерам

После изменения реестра сначала доведите имеющийся архив до общей последней сессии со SPY через безопасное автообновление. Новые тикеры без истории будут перечислены в `pending_bootstrap`. Для них подготовьте отдельный файл.

```bash
python scripts/bootstrap_prices.py --output staging/daily_prices.csv.gz
```

При ограничении Yahoo можно использовать `--limit 20` или выбрать конкретные тикеры через `--symbols MU HPE DELL`. Каждый успешный этап создаёт отдельный архив. Передайте его следующим этапом через `--archive` либо после проверки перенесите в `data/daily_prices.csv.gz`. За один запуск скрипт собирает весь выбранный набор или завершает работу без публикации. Для рабочего архива используйте `bootstrap_prices.py` и workflows безопасного обновления. Команда `scripts.update_data --mode prices` выполняет массовое историческое обновление и подходит для отдельной копии данных.

Для отката в тестовой ветке отмените коммит версии. Если после него появились обновления SEC или цен, сначала сохраните эти изменения в отдельной ветке.
