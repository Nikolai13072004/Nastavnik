# Запуск из исходников

Рядом должны лежать папки `prodigy` и `vedomo`. Команды выполняются из `prodigy`.
Нужны Docker с Compose и Buildx, Python, минимум 12 ГБ свободной RAM
и запас диска для образов. На ноутбуке не останавливать другие проекты.

## Подготовка один раз

Создать ограниченный builder, если его ещё нет. Существующий не заменять:

```sh
docker buildx create --name prodigy-max-build --driver docker-container --driver-opt memory=4g,memory-swap=4g,cpu-period=100000,cpu-quota=200000
python deploy/max/create-review-env.py --joint
```

`.env.joint-review` хранит локальные секреты. Генератор откажется заменять
существующий файл. Нужен кеш BGE-M3 версии
`5617a9f61b028005a4858fdac845db406aefb181` в томе `vedomo-max-local-models`.
Подготовка без кеша описана в [локальном гайде](../MAX_LOCAL_SETUP.md).
Файлы модели занимают около 2,3 ГБ. Этот запуск не скачивает их скрытно.

## Одна команда Docker

PowerShell, после подготовки:

```powershell
$env:BUILDX_BUILDER = 'prodigy-max-build'
$env:REVIEW_PRODIGY_IMAGE = 'prodigy-max:source-review-20260927'
$env:REVIEW_VEDOMO_IMAGE = 'vedomo-backend:source-review-20260927'
$sourceCompose = @('-p', 'prodigy-max-review-source-built-20260927', '--env-file', 'deploy/max/.env.joint-review', '-f', 'deploy/max/compose.review.yml', '-f', 'deploy/max/compose.joint-review.yml', '-f', 'deploy/max/compose.source-review.yml')
docker compose @sourceCompose up -d --build
```

Выбирать отдельный проект с префиксом `prodigy-max-review-`, не имя пилота VPS.
Ранее созданные базы не сбрасываются. Для нового пустого окружения выбрать
новое имя проекта. Не добавлять `--wait`: завершающиеся проверки здесь
останавливают зависимый `review-ready` при ошибке.

Команда собирает оба компонента, применяет миграции, создаёт внутренний TLS,
индексирует учебный документ и проверяет обучение по HTTPS. Также проверяются
HR-загрузка, публикация, автоматическая индексация, повтор и снятие публикации.
Успех:
`review-check` завершился с кодом 0, `review-ready` напечатал `REVIEW_READY`.

```powershell
docker compose @sourceCompose logs review-check review-ready
docker compose @sourceCompose stop --timeout 30
```

`stop` сохраняет базы и файлы. Не применять `down -v` или глобальный prune.
Порт 53100 доступен только на `127.0.0.1`; базы не публикуют порты.
На `/max` обычный браузер покажет инструкцию открытия из MAX.
Синтетические серверные профили проверки не дают обхода регистрации на VPS.

## Внешние интеграции

В этом режиме настоящие сообщения MAX и генерация AI выключены.
Поиск BGE-M3 и точный источник работают. Проверка с GigaChat и доверенным
сертификатом есть в [локальном гайде](../MAX_LOCAL_SETUP.md).
Нужны действующие API-права GigaChat и закрытый файл ключа, не Git.

Для настоящего входа MAX нужны зарегистрированный бот, токен,
HTTPS-адрес Mini App, подписанный запуск и личные учебные профили.
Публичный пилот и порядок ручного теста: [MAX_DEMO.md](../MAX_DEMO.md).

## Результат замера

27 сентября совместная сборка Prodigy и Vedomo без кеша слоёв заняла
245,26 секунды на builder 4 ГБ / 2 CPU. Базовые образы уже были загружены;
Debian, npm и pip установлены заново. Сборка образов уложилась в 5 минут.
Загрузка модели, запуск баз и проверка обучения в это время не включены.

Для повторного замера после подготовки:

```powershell
docker compose @sourceCompose build --no-cache web vedomo-api
```

Отдельные последовательные сборки заняли 135,99 и 220,99 секунды.
Для общего запуска использовать совместную сборку, не складывать эти времена.
