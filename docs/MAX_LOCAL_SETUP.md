# Локальный запуск Prodigy MAX

Общий запуск текущих исходников с автоматической подготовкой и проверкой:
[START.md](contest/START.md). Старые команды ниже полезны для отдельных проверок.

## Проверка сборки на пустой базе

Из папки `prodigy`, при запущенном Docker:

Если builder `prodigy-max-build` ещё не создан, один раз настройте лимиты:

```sh
docker buildx create --name prodigy-max-build --driver docker-container --driver-opt memory=4g,cpu-period=100000,cpu-quota=200000
```

```sh
python deploy/max/create-review-env.py
docker buildx build --builder prodigy-max-build --load -f Dockerfile.max -t prodigy-max:review-20260926 .
docker compose --env-file deploy/max/.env.review -f deploy/max/compose.review.yml up -d --no-build --pull never --wait
docker compose --env-file deploy/max/.env.review -f deploy/max/compose.review.yml run --rm --no-deps --entrypoint node web node_modules/tsx/dist/cli.mjs scripts/max-pilot-setup.ts setup
docker compose --env-file deploy/max/.env.review -f deploy/max/compose.review.yml run --rm --no-deps --entrypoint node web node_modules/tsx/dist/cli.mjs scripts/max-pilot-setup.ts assessment
docker compose --env-file deploy/max/.env.review -f deploy/max/compose.review.yml run --rm --no-deps --entrypoint node web node_modules/tsx/dist/cli.mjs scripts/max-pilot-setup.ts scope
```

Не заменяйте ограниченную сборку обычным `next build` или Turbopack на Windows.

`http://127.0.0.1:53100/max` показывает экран открытия из MAX.
`/login` и `/admin` должны вернуть 404. База не публикует порт, её том имеет
префикс `prodigy-max-review`. Миграции применяются отдельным сервисом до запуска web.
`setup` откажется работать, если база уже содержит посторонние данные.

Этот запуск проверяет сборку, схему и данные, но не подписанный вход MAX и не AI.
Токен бота намеренно пуст, исходящие запросы сети закрыты. Для настоящей проверки
откройте Mini App на HTTPS-стенде по [сценарию](MAX_DEMO.md).
Остановка без удаления данных: `docker compose --env-file deploy/max/.env.review -f deploy/max/compose.review.yml stop`.
Файл `.env.review` не публиковать; на Windows хранить в закрытой пользовательской папке.

## Общий учебный курс

После `setup`, `assessment` и `scope` можно добавить общий курс, не удаляя два
прежних. Используется только утверждённый учебный текст из репозитория.
Команды из `prodigy`, в PowerShell:

```powershell
docker compose --env-file deploy/max/.env.review -f deploy/max/compose.review.yml run --rm --no-deps -v "${PWD}/docs/drafts/onboarding-policy.md:/run/onboarding-policy.md:ro" web node node_modules/tsx/dist/cli.mjs scripts/import-max-demo-document.ts /run/onboarding-policy.md
docker compose --env-file deploy/max/.env.review -f deploy/max/compose.review.yml run --rm --no-deps web node node_modules/tsx/dist/cli.mjs scripts/max-onboarding-setup.ts --create-pilot-course
```

Ожидаемый результат: `CREATED`, затем `ALREADY_EXISTS` при повторном запуске.
Курс содержит обязательный материал, документ и тест из трёх вопросов.
AI отдельно требует Vedomo, сервисный ключ и mapping документа; эти команды
не включают AI и не подтверждают чистый запуск всей связки.

## Совместная проверка Prodigy и Vedomo

Этот режим проверяет пустые базы, настоящий поиск BGE-M3 и интеграцию через
HTTPS. Генерация AI и отправка в MAX отключены, реальные ключи не нужны.
Не подключать к рабочим базам VPS. Другие контейнеры должны сохранить свои порты;
порт 53100 нужен только этому стенду. Обычный review-стенд сначала остановить.

Подготовка: локальные образы `prodigy-max:chat-navigation-20260927`,
`vedomo-backend:max-knowledge-20260926`, `postgres:16-alpine` и
`nginx:stable-alpine`. Prodigy собирается через ограниченный builder выше,
с новым тегом. Vedomo собирается отдельно по его инструкции. Нужен существующий
том `vedomo-max-local-models` с кешем `BAAI/bge-m3`; он подключается только для
чтения. Без кеша режим завершится ошибкой, а не начнёт скачивать модель.
Совместная сборка образов без кеша слоёв проверена: 245,26 секунды на 2 CPU / 4 ГБ.
Базовые образы были загружены заранее. Модель и запуск сервисов считаются отдельно.
Для проверки уже собранных образов есть сценарий ниже.

Команды из папки `prodigy`, PowerShell:

```powershell
# Только при первом запуске. Существующие секреты не заменять.
python deploy/max/create-review-env.py --joint
$jointCompose = @('--env-file', 'deploy/max/.env.joint-review', '-f', 'deploy/max/compose.review.yml', '-f', 'deploy/max/compose.joint-review.yml')
docker compose @jointCompose up -d --no-build --pull never --wait --wait-timeout 180
docker compose @jointCompose exec -T vedomo-api python /run/setup-joint-review.py
docker compose @jointCompose run --rm -T --no-deps --entrypoint node web node_modules/tsx/dist/cli.mjs scripts/joint-review-smoke.ts
docker compose @jointCompose stop
```

Проверка создаёт учебный источник и связывает его по точному SHA-256. Проверяет
вход по паролю, права HR, обязательное чтение, одну попытку теста и отчёт;
свои временные профили удаляет. Повтор использует сохранённые учебные данные.
Локальный сервисный ключ действует сутки; после истечения нужен новый отдельный
review-стенд, а не замена секретов существующей базы.

Секреты находятся в `.env.joint-review`, он не публикуется. Собственные тома
начинаются с `prodigy-max-joint-review`; базы не открывают порты. Внутренний
сертификат используется только контейнерами, системное доверие не меняется.
`stop` сохраняет данные. Не применять глобальный prune или удаление тома моделей.

## Изолированный запуск с GigaChat

Нужны локальные образы Prodigy, Vedomo, PostgreSQL и nginx. Скрипт не собирает
и не скачивает образы автоматически. Сборка Prodigy ограниченным builder:

```powershell
docker buildx build --builder prodigy-max-build --load -f Dockerfile.max -t prodigy-max:tester-ready-v2-20260927 .
```

Ключ GigaChat храните отдельно от Git. Закрытый env-файл должен содержать
`GIGACHAT_AUTH_KEY`, `GIGACHAT_SCOPE` и `GIGACHAT_MODEL`. Отдельно нужен доверенный
PEM-сертификат. Проверку TLS не отключать.

```powershell
python deploy/max/run-joint-review.py --project prodigy-max-review-gigachat --prodigy-image prodigy-max:tester-ready-v2-20260927 --gigachat-env C:/private/gigachat.env --gigachat-ca C:/private/gigachat-ca.pem
```

Пути `C:/private/...` заменить своими. По умолчанию используется существующий
том моделей `vedomo-max-local-models`, только для чтения. Для новой модели
добавить `--download-model`: создастся отдельный том. Зафиксирована версия
BGE-M3; необходимые файлы около 2,3 ГБ. При объёме больше 5 ГБ скрипт откажется
скачивать без отдельного разрешения. Механизм загрузки описан в
[документации Hugging Face](https://huggingface.co/docs/huggingface_hub/guides/download).

Имя проекта должно быть новым, с префиксом `prodigy-max-review-`. Существующие
контейнеры, базы и секреты не перезаписываются. Проверяются миграции, источник,
обучение, настоящий ответ GigaChat и отказ без доступа. После проверки свои
контейнеры останавливаются, тома сохраняются. Ничего в настоящий MAX не
отправляется. Это не проверка интерфейса телефона и не замер сборки из исходников.

## Разработка

Команды выполняются из папки `prodigy`. Нужен локальный `.env` с параметрами отдельной dev-БД. Для установки зависимостей нужен интернет; запускать `npm ci` только при необходимости.

```powershell
npm ci
docker compose -f compose.max-dev.yml up -d --wait
npx prisma migrate deploy
npm run dev:safe
```

Приложение открывается на `http://localhost:3101/max`, проверка процесса - на `http://localhost:3101/api/health`. Локальная PostgreSQL доступна только через `127.0.0.1:55434`. Не использовать эту базу и её секреты для публичного стенда.

## Важные ограничения

- На Windows используйте `npm run dev:safe`. Он запускает Webpack с ограничениями для дочерних процессов. Порт 3100 занят другим проектом; не останавливайте его ради Prodigy.
- Не запускайте исходный `prisma/seed.ts` на существующей базе: он удаляет данные и создаёт простые демонстрационные пароли.
- Для остановки локальной БД без удаления данных: `docker compose -f compose.max-dev.yml stop`. Не используйте `down -v` для рабочего стенда.
- Токен бота и другие секреты храните только в окружении. Не добавляйте их в документы, логи или репозиторий.

## MAX и привязка

По умолчанию HR выдаёт личный одноразовый код; сотрудник вводит его внутри MAX.
Код действует 15 минут и связывает только его профиль. Входить в веб-LMS для
обучения не нужно. Необязательный путь через `/connect-max` включается явно
через `MAX_LMS_LINKS=enabled`. MAX-сессия не создаёт веб-сессию и не выдаёт права HR.

Для проверки токена бота укажите `MAX_BOT_TOKEN` и `MAX_BOT_USERNAME`, затем выполните `npm run max:check`. Команда проверяет бота через GET `/me` и ничего не публикует. Отключать проверку TLS при ошибке сертификата нельзя.

Webhook записывает `bot_started` в очередь. Если отправка приветствия зависла в состоянии `SENDING` или `UNCERTAIN`, сначала проверьте фактическую доставку в MAX. Не возвращайте такие записи автоматически в `PENDING`: это может отправить сообщение повторно.

Проверки кода: `npm run test:unit`, `npm run lint`, `npx tsc --noEmit --incremental false`. Публичный стенд описан в [deploy/max/README.md](../deploy/max/README.md).
