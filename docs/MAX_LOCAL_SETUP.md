# Локальный запуск Prodigy MAX

## Проверка сборки на пустой базе

Из папки `prodigy`, при запущенном Docker:

Если builder `max-review-build` ещё не создан, один раз настройте лимиты:

```sh
docker buildx create --name max-review-build --driver docker-container --driver-opt memory=4g,cpu-period=100000,cpu-quota=200000
```

```sh
python deploy/max/create-review-env.py
docker buildx build --builder max-review-build --load -f Dockerfile.max -t prodigy-max:review-20260926 .
docker compose --env-file deploy/max/.env.review -f deploy/max/compose.review.yml up -d --wait
docker compose --env-file deploy/max/.env.review -f deploy/max/compose.review.yml exec web node node_modules/tsx/dist/cli.mjs scripts/max-pilot-setup.ts setup
docker compose --env-file deploy/max/.env.review -f deploy/max/compose.review.yml exec web node node_modules/tsx/dist/cli.mjs scripts/max-pilot-setup.ts assessment
docker compose --env-file deploy/max/.env.review -f deploy/max/compose.review.yml exec web node node_modules/tsx/dist/cli.mjs scripts/max-pilot-setup.ts scope
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

Сотрудник получает одноразовый код в `/connect-max` после входа в LMS и вводит его в Mini App. Код действует 15 минут и связывает только его собственный профиль. MAX-сессия не заменяет LMS-вход и не выдаёт права HR.

Для проверки токена бота укажите `MAX_BOT_TOKEN` и `MAX_BOT_USERNAME`, затем выполните `npm run max:check`. Команда проверяет бота через GET `/me` и ничего не публикует. Отключать проверку TLS при ошибке сертификата нельзя.

Webhook записывает `bot_started` в очередь. Если отправка приветствия зависла в состоянии `SENDING` или `UNCERTAIN`, сначала проверьте фактическую доставку в MAX. Не возвращайте такие записи автоматически в `PENDING`: это может отправить сообщение повторно.

Проверки кода: `npm run test:unit`, `npm run lint`, `npx tsc --noEmit --incremental false`. Публичный стенд описан в [deploy/max/README.md](../deploy/max/README.md).
