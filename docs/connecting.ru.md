# Подключение к существующему приложению

Если у вас уже есть код, вызывающий SDK какой-либо LLM, вам не нужно
самостоятельно считать токены или стоимость — у `CostTracker` есть адаптер
под каждого провайдера, который читает usage прямо из объекта ответа.

## SDK-адаптеры

```python
# OpenAI
response = openai_client.chat.completions.create(...)
tracker.log_openai_response(response, label="chat")

# Anthropic
response = anthropic_client.messages.create(...)
tracker.log_anthropic_response(response, label="chat")

# Gemini (google-genai)
response = gemini_client.models.generate_content(...)
tracker.log_gemini_response(response, label="chat")

# Ollama — у локальных моделей обычно нет записи в pricing.json, поэтому
# передавайте cost=0.0 (или свой pricing=); при стриминге передавайте только
# последний чанк.
response = ollama_client.chat(...)
tracker.log_ollama_response(response, label="chat", cost=0.0)

# LangChain — читает AIMessage.usage_metadata (актуальный langchain-core),
# либо откатывается на более старую форму LLMResult.llm_output["token_usage"].
result = chat_model.invoke(...)
tracker.log_langchain_result(result, label="chat")
```

**LiteLLM**: отдельный адаптер не нужен — `litellm.completion(...)`
возвращает `ModelResponse`, приводящий любого провайдера к одной и той же
OpenAI-совместимой форме, поэтому `log_openai_response(response, label="chat")`
уже работает как есть.

Ни один из этих адаптеров не добавляет SDK провайдера в зависимости
`llm-burnwatch` — они читают поля из того объекта ответа, который уже есть
в вашем коде, в момент вызова.

Каждый адаптер учитывает собственные правила биллинга кэшированных токенов
у конкретного провайдера (вычитаемые vs аддитивные счётчики), поэтому
`cached_input_tokens` всегда означает «оплачено по более дешёвому
кэшированному тарифу», независимо от провайдера.

### Отсутствующий или устаревший прайсинг

Если в упакованном `pricing.json` нет модели или тариф устарел, передайте
точечные переопределения вместо ручного копирования всего файла:

```python
tracker = CostTracker(
    "calls.jsonl",
    pricing_overrides={"my-model": {"input_per_1m": 3.0, "output_per_1m": 9.0}},
)
```

`pricing_overrides` объединяется поверх встроенных значений по умолчанию
(всё остальное остаётся как в поставке); если вы хотите заменить всю таблицу
целиком — передайте `pricing=`, оба параметра взаимоисключающие.

Можно также явно и по требованию подтянуть по сети файл с ценами,
поддерживаемый сообществом:

```bash
llm-burnwatch pricing import https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
```

Это **единственная** команда llm-burnwatch, которая вообще делает сетевой
вызов, и только когда ей передан `http(s)://` URL — локальный путь к файлу
сеть никогда не трогает. Импортируйте только из источника, которому
доверяете; см. [Модель безопасности](security.ru.md#граница-доверия-pricing-import)
о том, что именно это защищает, а что нет.

## Уже отправляете трассировки OpenTelemetry GenAI?

Если ваше приложение уже испускает спаны по
[семантическим соглашениям OpenTelemetry GenAI](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
(например через OpenLLMetry или другую GenAI-инструментацию), вам вообще не
нужно добавлять вызовы `CostTracker` — импортируйте уже имеющийся экспорт:

```bash
llm-burnwatch import otel traces.json --log-file calls.jsonl
```

- Принимает сырую форму экспорта OTLP JSON (`resourceSpans` → `scopeSpans` →
  `spans`) как единый JSON-объект, JSON-массив таких объектов, либо JSONL
  (по объекту на строку — типичный формат, который пишет файловый экспортёр
  OTel Collector).
- **Только локальный путь к файлу** — в отличие от `pricing import`, эта
  команда не принимает `http(s)://` URL. Это разовый пакетный импорт уже
  имеющегося на диске экспорта, а не вторая сетевая граница.
- Устойчива к обоим поколениям именования атрибутов, встречавшимся в
  спецификации: текущему (`gen_ai.request.model`,
  `gen_ai.usage.input_tokens`/`output_tokens`) и более старому/в стиле
  OpenLLMetry (`gen_ai.usage.prompt_tokens`/`completion_tokens`).
- Устойчива к спанам, вообще не несущим распознаваемых атрибутов `gen_ai.*` —
  реальный экспорт трассировок ожидаемо содержит немало не-GenAI спанов
  (HTTP-хендлеры, вызовы БД...), которые молча пропускаются, а не
  считаются ошибкой.
- Модель, отсутствующая в `pricing.json`, импортируется с `cost_micros=0` и
  одноразовым предупреждением, вместо прерывания всего пакета из-за одной
  нераспознанной модели.

## Сквозной пример

[`examples/e2e_actions_demo.py`](https://github.com/chemodannebro-rgb/llm-burnwatch/blob/main/examples/e2e_actions_demo.py)
связывает вместе адаптер LangChain, месячный бюджет, `detect --follow` и
вебхук-приёмник на реальном локальном HTTP-сервере:

```bash
python examples/e2e_actions_demo.py
```

## Формат лога

Каждая строка лога — один JSON-объект; полный контракт (обязательные поля,
типы, необязательные поля вроде `cached_input_tokens`/`trace_id`) описан в
`src/llm_burnwatch/schema.json`, доступен также через `llm-burnwatch schema`.
Это источник истины для любого не-Python клиента (Node.js, Go...), который
хочет писать совместимый лог — каждая запись несёт `schema_version` на
случай будущих изменений формата, плюс UTC `timestamp` (ISO 8601) момента
вызова.

Каждой записи нужен `label` (твоё собственное имя точки вызова, например
`"retrieval"`/`"summarize"`) и идентификатор `model`, по которому
выставляется счёт, вместе с `input_tokens`/`output_tokens`/`cost_micros`.
Необязательный свободный объект `extra` позволяет прикрепить свои
метаданные (например, `workflow_id`), не меняя схему.

`cost_micros` — целое число (1 micro = $0.000001), а не число с плавающей
точкой в долларах, чтобы не округлять вызов на $0.0025 до $0.00 и избежать
накопления погрешности при суммировании большого лога.

Токены рассуждений (модели в стиле o1/o3) не выделены в отдельное поле —
считай их в `output_tokens`, по той же ставке.

## Куда дальше

- Хотите, чтобы детекция ловила зацикливание/скачки стоимости/смену модели
  по мере их появления? См. страницы [Детекторы](detectors/baseline.ru.md).
- Хотите останавливать зацикливание на лету, а не только обнаруживать его
  постфактум? См. [budget vs guard()](budget-vs-guard.ru.md).
