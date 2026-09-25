# Инструкции для AI-агентов

Эти правила действуют для всего репозитория.

## Совместная работа и GitHub

Над проектом могут параллельно работать несколько разработчиков и AI-агентов.

- Перед началом работы выполните `git status`, `git fetch origin --prune`, `gh pr list` и просмотрите релевантные удалённые ветки/PR. Если работа пересекается с чужой, сначала изучите её актуальный diff и согласуйте границы.
- Не коммитьте и не пушьте напрямую в `main`. Изменения доставляются из отдельной рабочей ветки через pull request.
- Не пушьте в ветку другого разработчика или агента без явного согласования. Не включайте в свой коммит уже лежащие в worktree чужие изменения.
- Ветка и PR могут измениться во время ревью. Перед правками по ревью, финальной проверкой или разрешением конфликта снова сделайте `git fetch` и проверьте текущий PR/remote branch; не опирайтесь на старый локальный diff.
- Не делайте commit, push, rebase или merge, если пользователь этого явно не просил. Не удаляйте и не откатывайте чужие изменения ради «чистого» worktree.
- По требованию владельца не добавляйте AI-инструмент в авторство, Co-authored-by, подписи коммитов или PR. Не меняйте Git identity без согласования. Перед установкой новых инструментов предупреждайте пользователя.

## Production-схема: PostgreSQL + `migrate deploy`

База — **PostgreSQL**. В `Dockerfile` схема применяется через `npx prisma migrate deploy` поверх чистой истории миграций (baseline `*_init_postgres`). Прежнее правило «только `db push`, не `migrate deploy`» (ADR-010, `deploy-guard`) **снято** — оно относилось к старой SQLite-базе без `_prisma_migrations` (P3005). Обоснование и процедура переезда: `docs/architecture/014-postgres-migration-plan.md`. Старые SQLite-миграции — в `prisma/migrations-sqlite-archive/` (на PG не применяются).

При изменении Prisma-схемы поддерживайте в согласованном состоянии `prisma/schema.prisma` и `prisma/migrations/` и проверяйте drift:

```bash
npx prisma migrate diff --from-migrations prisma/migrations --to-schema-datamodel prisma/schema.prisma --exit-code
```

MAX-стенд использует отдельную PostgreSQL и только свою историю migrate deploy. Старые указания на SQLite не относятся к текущей схеме.

## Архитектурные границы

- Перед изменением поведения прочитайте релевантный ADR в `docs/architecture/`. Принятые решения там обязательны, а не справочны.
- Бизнес-модули находятся в `src/modules/{learning,assessment,enrollment,content,course,outbox}`. Направление зависимостей: `UI/HTTP -> server composition -> infrastructure -> application -> domain`.
- `domain` должен оставаться чистым: без Next.js, React, Prisma и серверной инфраструктуры. `application` зависит от инфраструктуры только через порты. Транзакции реализуются адаптерами репозиториев; Route Handlers и Server Actions остаются тонкими адаптерами авторизации, разбора входа, вызова use case и отображения результата.
- Не возвращайте прямые Prisma-записи и бизнес-правила в UI/route/action. Не создавайте снова общий монолит `course-actions.ts`: используйте предметный адаптер и модуль-владелец. Read-side course management может объединять данные, но не мутировать их и не владеть доменными правилами.

## Next.js 16

- Локальный dev на Windows запускать через `npm run dev:safe` (порт 3101, Webpack, Windows Job Object: максимум 12 процессов, 4 GiB committed memory и 20% CPU на всё дерево). Порт 3100 занят другим проектом; не останавливать его. Скрипт запускается в отдельном PowerShell, не dot-source. Не запускать напрямую `next dev` / Turbopack: 23 сентября сборка Tailwind зациклилась и породила около 2000 процессов. `npm run dev` тоже использует Webpack, но без системных лимитов. Production build пока не проверен; не запускать стандартный Turbopack build без отдельного безопасного плана. Не удалять внешние lockfile пользователя.

Текущая версия Next.js — `16.3.6` (обновлена с 16.2.2 перед HTTPS-стендом). Перед написанием Next.js-кода прочитайте релевантный раздел локальной документации в `node_modules/next/dist/docs/` и учитывайте deprecation notices; не полагайтесь на память о предыдущих версиях. MAX Docker image собирается только через Dockerfile.max и отдельный ограниченный builder; на общем VPS сборку не запускать. Операционные правила: deploy/max/README.md.

## Файловое хранилище

- Для `public/uploads` и `public/branding` используйте `src/lib/storage`, а не ручную склейку путей. Он проверяет ключи, не допускает path traversal и корректно преобразует локальные URL.
- Для новых загрузок, где нужна дедупликация и запись метаданных `StorageFile`, используйте `putBufferDedup`/`putStreamDedup` из `src/lib/storage/dedup`. При дедупликации возвращённый `object.key` может отличаться от запрошенного — сохраняйте именно возвращённый объект.
- В Docker база, uploads и branding находятся в отдельных persistent volumes (`lms_data`, `lms_uploads`, `lms_branding`). Не считайте эти каталоги временными и не меняйте их размещение без проверки web-приложения и воркеров.

## Тесты и проверки

Скрипт `test:unit` автоматически находит unit-тесты через scripts/run-unit-tests.mjs; infra запускаются отдельно. Не использовать полный test:infra на существующей dev/пилотной базе: runner может сбрасывать данные. Перед передачей изменений запускайте подходящий минимум из:

```bash
npm run test:unit
npm run lint
npx tsc --noEmit --incremental false
```

## SMTP и секреты

Для реальной доставки почты источник истины — deployment environment / `.env`: `EMAIL_PROVIDER`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURE`, `SMTP_TLS_SERVERNAME`, `SMTP_USER`, `SMTP_PASS`, `SMTP_FROM` и OAuth SMTP variables, если включён OAuth2.

Не меняйте и не перезаписывайте эти значения defaults из admin/platform и не печатайте секреты. При тестировании реальной доставки используйте значения окружения напрямую, если пользователь явно не попросил проверить сохранённые admin settings.

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
