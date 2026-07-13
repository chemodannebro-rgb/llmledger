# Публичный API

Эта страница — единственное место, где явно сказано, что покрывается
семверными обязательствами llm-burnwatch (см.
[Versioning](https://github.com/chemodannebro-rgb/llm-burnwatch/blob/main/CONTRIBUTING.md#versioning)
в `CONTRIBUTING.md`), а что нет. Если чего-то нет в списке ниже — это
внутреннее: форма может измениться или исчезнуть в minor/patch-релизе без
предупреждения об устаревании, даже если технически это можно
импортировать уже сегодня.

## Python API

Всё, что импортируется из корня пакета:

```python
from llm_burnwatch import CostTracker, BudgetExceededError, __version__
```

### `CostTracker`

```python
CostTracker(
    log_file=None,          # по умолчанию default_log_path(), если не задан
    *,
    pricing=None,            # полная замена таблицы цен
    pricing_overrides=None,  # точечные переопределения поверх встроенного pricing.json
    max_bytes=10 * 1024 * 1024,
    backup_count=5,
)
```

`pricing` и `pricing_overrides` взаимно исключают друг друга — если
передать оба, будет `ValueError`. Когда что использовать — см.
[Подключение к существующему приложению](connecting.ru.md#отсутствующий-или-устаревший-прайсинг).

| Метод | Назначение |
| --- | --- |
| `log_call(*, label, model, input_tokens, output_tokens, cached_input_tokens=0, cost=None, pricing=None, trace_id=None, **extra)` | Залогировать один вызов. Возвращает записанный JSONL-словарь. |
| `log_openai_response(response, *, label, model=None, trace_id=None, **extra)` | Адаптер: читает `response.usage` (формат OpenAI SDK или эквивалентный dict). |
| `log_anthropic_response(response, *, label, model=None, trace_id=None, **extra)` | Адаптер: читает `response.usage` (формат Anthropic SDK). |
| `log_gemini_response(response, *, label, model=None, trace_id=None, **extra)` | Адаптер: читает `response.usage_metadata` (формат SDK `google-genai`). |
| `log_ollama_response(response, *, label, model=None, trace_id=None, **extra)` | Адаптер: читает `prompt_eval_count`/`eval_count` прямо с ответа (у Ollama нет объекта `usage`). Передавайте финальный чанк потока, не промежуточный. |
| `log_langchain_result(result, *, label, model=None, trace_id=None, **extra)` | Адаптер: читает `result.usage_metadata` (текущий LangChain) либо, если его нет, `result.llm_output["token_usage"]` (старый `LLMResult`). |
| `guard(*, trace_id=None, max_usd_per_trace=None, max_calls_per_trace=None)` | Контекстный менеджер; бросает `BudgetExceededError` из того вызова `log_call()`/адаптера, который выводит trace с совпадающим `trace_id` за заданный лимит. Внутрипроцессное, потрейсовое принуждение — не тот же механизм, что `budget`/`BudgetDetector` (межпроцессный, помесячный, постфактум-анализ). См. [budget vs guard()](budget-vs-guard.ru.md). |
| `report()` | Возвращает ту же структурную сводку, что и `llm-burnwatch report --json` для лога этого экземпляра (нули/пустые разбивки на пустом логе, не ошибка). |
| `total_cost()` | Сокращение для `report()["total_cost_usd"]`. |

Каждый SDK-адаптер в итоге вызывает `log_call(...)` — любой адаптер может
бросить `BudgetExceededError` точно так же, как `log_call()`, если активен
подходящий блок `guard()`.

### `BudgetExceededError`

Бросается `guard()` (и через него — `log_call()`/любым адаптером), когда
защищённый trace превышает свой лимит. Вызов, который это вызвал, уже
залогирован — это сигнал прекратить делать дальнейшие вызовы в этом trace,
а не способ отменить уже произошедший. Полное обоснование — в докстринге
самого исключения.

### `__version__`

Совпадает с `version` из `pyproject.toml` (синхронизируются вручную — см.
раздел Versioning в `ARCHITECTURE.md`). То же значение печатает
`llm-burnwatch --version`.

## CLI

Одиннадцать подкоманд (`llm-burnwatch <command> --help` — полный список
флагов любой из них; в этой таблице — только те флаги, что нужны
большинству пользователей):

| Команда | Назначение | Ключевые флаги | Коды выхода |
| --- | --- | --- | --- |
| `report` | Сводка по стоимости из лога. По умолчанию — последние 30 дней. | `--log-file` (обязателен), `--all-time`, `--since`/`--until`, `--trace-id`, `--json`, `--format text\|csv`, `--fx-rate`/`--currency` (`--rub-rate` — устаревший предшественник) | `0` успех, `2` лог не найден/несовместимые флаги |
| `dashboard` | Записать статичный самодостаточный HTML-дашборд стоимости. | `--log-file`, `--out` (оба обязательны), `--since`/`--until`, `--fx-rate`/`--currency` | `0` успех, `2` лог не найден |
| `demo-data` | Записать синтетический демо-лог (чтобы попробовать `detect`/`report` без реального трафика). | `--out` (обязателен), `--n-normal`, `--n-anomalies`, `--seed` | `0` успех |
| `detect` | Один прогон всех детекторов по логу, либо непрерывный поток алертов с `--follow`. | `--log-file` (обязателен), `--sensitivity low\|normal\|high` (по умолчанию `normal`; взаимоисключим с продвинутым `--threshold`), `--allowed-models`, `--max-call-cost`, `--max-trace-cost`, `--frequency-detector auto\|on\|off`, `--cusum-detector on\|off`, `--json`, `--follow` (+ `--poll-interval`, `--webhook-url`, `--slack-webhook-url`, `--telegram-bot-token`/`--telegram-chat-id`, `--exec-sink`) | `0` ничего не найдено, `1` найдено (аномалии/нарушения правил/всплески частоты/сдвиги уровня/алерты бюджета), `2` лог не найден |
| `status` | Показать, какие гейтуемые детекторы (`frequency`/`cusum`/`budget`) включены/выключены/учатся для лога, без запуска самого поиска аномалий. | `--log-file` (обязателен), `--json` | `0` успех, `2` лог не найден |
| `train` | Обучить опциональную ML-модель поиска аномалий (экстра `llm-burnwatch[anomaly]`, требует scikit-learn). | `--log-file` (обязателен), `--model-dir`, `--keep-last`, `--contamination` | `0` успех, `2` нет экстры / лог не найден / лог пуст |
| `schema` | Напечатать упакованную JSONL-схему лога (`schema.json`). | — | `0` |
| `validate` | Проверить лог против `schema.json`, либо (`--alerts`) проверить файл вывода `detect --json` против `alert_schema.json`. | `--log-file`, `--json`, `--alerts` + `--alerts-file` | `0` всё валидно, `1` найдены невалидные записи, `2` файл не найден/битый |
| `pricing import <source>` | Импортировать цены по модели из локального файла или URL `http(s)://` (формат цен LiteLLM) в пользовательский конфиг цен. **Единственная команда, которая вообще делает сетевой запрос**, и только для явного URL — см. [Модель безопасности](security.ru.md#граница-доверия-pricing-import). | — | `0` успех, `2` ошибка импорта |
| `budget set` / `budget show` | Настроить/посмотреть месячный бюджет в USD, который учитывают `BudgetDetector` команды `detect` и раздел Budget команды `report`. | `set --monthly --warn-at` | `0` |
| `import otel <source>` | Импортировать экспорт трейсов OpenTelemetry GenAI (только локальный файл) в лог. | `--log-file` (обязателен) | `0` успех, `2` ошибка импорта |

Соглашение о кодах выхода для всех команд: `0` успех/ничего не найдено,
`1` найдено что-то, требующее внимания (только `detect` и `validate`), `2`
ошибка использования (неверные флаги, файл не найден/не читается,
отсутствует опциональная зависимость). Непредвиденная внутренняя ошибка
(баг, а не ошибка пользователя) тоже возвращает `2`, с сообщением,
указывающим на трекер задач, а не сырым traceback.

## Ключи вывода `--json`

### `report --json`

`call_count`, `total_cost_micros`, `total_cost_usd`, `by_label_micros`,
`by_model_micros`, `pricing_last_updated`, `period` (`{since, until,
all_time}` — отражает *действующий* период, включая дефолтное 30-дневное
окно, если ни один флаг периода не был передан). Присутствуют только при
необходимости: `fx_rate`/`currency`/`total_cost_fx` (либо устаревшие
`rub_rate`/`total_cost_rub`), `budget`.

### `detect --json`

`alert_schema_version`, `call_count`, `threshold` (действующий, с учётом
sensitivity, порог baseline-детектора), `sensitivity`, `anomaly_count`,
`insufficient_data_count`, `anomalies` (в каждом: `index`, `label`,
`model`, `timestamp`, `features`), `rule_violation_count`,
`rule_violations`, `seasonal_baseline` (`{available, message}`),
`frequency_detector_enabled`, `frequency_spike_count`,
`frequency_spikes`, `cusum_detector_enabled`, `level_shift_count`,
`level_shifts`, `budget_detector_enabled`, `budget_alert_count`,
`budget_alerts`, `ml` (присутствует только если доступна опциональная
экстра `[anomaly]` и обученная модель).

`sensitivity` добавлен как чисто аддитивный ключ (см. собственную
политику аддитивных ключей `alert_schema.json`) — это не бампит
`alert_schema_version`, и ни один существующий ключ не поменял смысл.

### `status --json`

`call_count`, `detectors` (список `{name, state, message}` — `state` это
одно из `on`/`off`/`learning`).

### `validate --json` / `validate --alerts --json`

`record_count`/`invalid_count`/`invalid` (обычная валидация лога) либо
`valid`/`errors` (режим `--alerts`).

## Замороженные контракты

Эти не меняют форму внутри одной мажорной версии без бампа
`alert_schema_version`/`schema_version` (или, для остального —
задокументированного в CHANGELOG цикла устаревания по разделу Versioning
`CONTRIBUTING.md`):

- **`schema.json`** — контракт записи JSONL-лога (`schema_version:
  "1.0"`, также печатается `llm-burnwatch schema`).
- **`alert_schema.json`** — контракт объекта алерта `detect --json`/
  `detect --follow` (`alert_schema_version: 1`). Новые ключи можно
  добавлять аддитивно без бампа версии; существующие ключи не меняют
  смысл.
- **NDJSON-поток `detect --follow`** — один объект алерта на строку, те
  же поля, что и одна запись массивов `anomalies`/`rule_violations`/и
  т.д. из `detect --json`.
- **Имена переменных окружения** — `XDG_CONFIG_HOME`, `XDG_DATA_HOME`,
  `LLM_BURNWATCH_WEBHOOK_URL`, `LLM_BURNWATCH_SLACK_WEBHOOK_URL`,
  `LLM_BURNWATCH_TELEGRAM_BOT_TOKEN`, `LLM_BURNWATCH_TELEGRAM_CHAT_ID`.
  Что именно покрывает доверительная граница каждой — см.
  [Модель безопасности](security.ru.md).
- **Имена подкоманд и флагов CLI**, перечисленные в таблице выше
  (добавление нового опционального флага — не breaking change; удаление
  или изменение смысла существующего — да, и следует политике устаревания
  из `CONTRIBUTING.md`).

## Внутреннее (не покрыто семвером)

Всё остальное, включая (но не ограничиваясь): `detectors/*` (классы
детекторов, `run_detectors()`, `DEFAULT_REGISTRY`), `anomaly/*`
(baseline-статистика, извлечение фич, обучение/реестр ML),
`logreader.py`, `follow_state.py`, внутренности рендеринга
`dashboard.py`, и любая функция/класс `cli.py`/`tracker.py`, не
перечисленные выше (включая те, что без ведущего подчёркивания, например
`build_report()`, `default_log_path()`, `user_pricing_path()`,
`user_budget_path()`, `resolve_pricing()`, `merge_pricing_overrides()`,
`load_default_pricing()`) — читать полезно, полагаться на них между
релизами без проверки CHANGELOG — небезопасно.
