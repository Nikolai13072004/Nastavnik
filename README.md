## Prodigy в MAX: конкурсный пилот

Prodigy хранит сотрудников, курсы и результаты. Здесь также находятся бот,
Mini App и HR-экраны MAX. [Vedomo](https://github.com/clapzy2/effective-business-max-vedomo/tree/feature/max-review)
отвечает за AI по утверждённым документам; для приватного репозитория нужен доступ.
PR #2 слит. Последние исправления отправлены в `feature/max-foundation` через
PR #3; автоматические проверки прошли. Это ещё не зафиксированная конкурсная версия.

Для проверки найдите `@se14424319_bot` в MAX и откройте Mini App.
[Сценарий и ожидаемые результаты](docs/MAX_DEMO.md),
[гайд для жюри](docs/contest/JURY_GUIDE.md), [проверка обучения и HR](docs/MAX_TESTING.md),
[полный roadmap](docs/MAX_CONTEST_ROADMAP.md), [статус](docs/MAX_STATUS.md).
[Пакет API и учебные данные](docs/contest/README.md).
[Docker-запуск, зависимости и остановка](docs/contest/START.md).
Новый тестовый профиль связывается одноразовым кодом от администратора стенда.
Обычная ссылка в браузере не заменяет вход через MAX.

Клиент MAX: `src/app/max` и `src/app/api/max`.
Локальная разработка: [MAX_LOCAL_SETUP.md](docs/MAX_LOCAL_SETUP.md).
Рабочий пилот: [deploy/max/README.md](deploy/max/README.md).
В демо только вымышленные данные; это не коммерческий релиз LMS.

Ниже остаётся общая документация Prodigy. Для конкурсного стенда используйте
инструкции MAX выше, а не старые команды запуска с SQLite.

## Public Access

Приложение публикуется через общий Traefik из Docker network `traefik-public`
(она `external` — Traefik живёт вне этого репозитория). Контейнер не открывает
host-порт `3000`; Traefik проксирует запросы на внутренний порт контейнера `3000`.

Развёртывание разведено на два контура — это **разные compose-файлы**, и они
намеренно отличаются, а не расходятся по ошибке:

- **Локально и сборка — корневой `docker-compose.yml`.** Образ собирается на месте
  (`build:`), роутер повешен на entrypoint `web` **без TLS**: HTTPS на этом контуре
  снимает внешний шлюз. Хост задаёт `LMS_PUBLIC_HOST`.

  ```bash
  docker compose up -d --build
  ```

- **Стенд и прод — [`deploy/compose.yml`](deploy/compose.yml).** Образ берётся из
  registry и не пересобирается, окружение задаётся переменными выкатки. **TLS здесь
  включён по умолчанию** (`TRAEFIK_ENTRYPOINT=websecure`, `TRAEFIK_TLS=true`), а
  `APP_BASE_URL`/`NEXTAUTH_URL`/`AUTH_URL` формируются как `https://${APP_HOST}`.
  Публичный домен задаёт `APP_HOST` (обязателен), **не** `LMS_PUBLIC_HOST`.

Значения `lms.example.com` в обоих файлах — безопасный документальный домен, а не
адрес готового стенда. Для выпуска TLS-сертификата нужна DNS-запись `A`/`AAAA` для
выбранного host.

## Local Development

Для локальной разработки без Docker:

```bash
npm run dev
```

И открыть `http://localhost:3000`.

Для фонового запуска dev-сервера на `3002`:

```bash
npm run dev:3002
```

Перед запуском команда проверяет рабочую БД, наличие пользователя `student123`, количество пользователей/курсов и обязательные колонки схемы.

Проверить БД отдельно:

```bash
npm run db:check
```

Локальная SQLite-БД находится в `prisma/dev.db`. В `.env` это выглядит как `file:./dev.db`, потому что Prisma резолвит путь относительно `prisma/schema.prisma`.

Лог запуска dev-сервера: `tmp/dev-server-3002.log`.

## Notes

- Не добавляйте `ports: - "3000:3000"` или другие прямые публикации приложения на `0.0.0.0`.
- Публичный HTTPS терминирует Traefik прод-контура (`deploy/compose.yml`, entrypoint `websecure`) — не корневой compose. В текущем серверном стеке его HTTPS entrypoint слушает host-порт `8443`; для обычного URL без порта нужен свободный host-порт `443` или внешний прокси, который направляет трафик на Traefik.
