# Локальная проверка из исходников

Команды выполняются из корня репозитория Наставника. Второй репозиторий не
нужен: приложение и сервис знаний находятся здесь. Нужны Docker Compose,
Buildx, Python, свободное место для образов и модель поиска BGE-M3. Локальный
запуск не отправляет сообщения в настоящий MAX и не проверяет генерацию GigaChat.

## Подготовка

Один раз создайте локальное окружение:

```powershell
python deploy/max/create-review-env.py --joint
```

Генератор не заменит существующий `.env.joint-review`. Секреты в Git не
добавляйте. Образы можно собирать стандартным Buildx builder. Если компьютеру
нужен ограниченный builder, настройте его отдельно и укажите в
`BUILDX_BUILDER`. Подготовка модели при пустом кеше описана в
[локальном гайде](../MAX_LOCAL_SETUP.md); её загрузка может занимать гигабайты.

## Запуск

```powershell
$env:REVIEW_PRODIGY_IMAGE = 'nastavnik-app:local'
$env:REVIEW_VEDOMO_IMAGE = 'nastavnik-knowledge:local'
$reviewCompose = @('-p', 'nastavnik-review', '--env-file', 'deploy/max/.env.joint-review', '-f', 'deploy/max/compose.review.yml', '-f', 'deploy/max/compose.joint-review.yml', '-f', 'deploy/max/compose.source-review.yml')
docker compose @reviewCompose up -d --build
docker compose @reviewCompose logs review-check review-ready
```

В именах некоторых переменных и сервисов пока сохранены прежние технические
идентификаторы для совместимости с действующим стендом. Они не обозначают
отдельные репозитории. Используйте отдельное имя Compose-проекта, если нужна
пустая проверочная база. Не используйте имя рабочего проекта на VPS.

Запуск собирает оба образа, применяет миграции к отдельным базам, поднимает
внутренний HTTPS, индексирует учебный документ и проверяет вход, доступ,
публикацию документа, обучение и HR-результат. Успех подтверждается строкой
`Joint review passed` в `review-check` и `REVIEW_READY` в `review-ready`.
Первый прогон индексации может занять больше времени; при ошибке смотрите
этап в логах `review-check`, не считайте один успешный повтор доказательством
стабильности холодного запуска.

После проверки остановите только этот проект:

```powershell
docker compose @reviewCompose stop --timeout 30
```

`stop` сохраняет базы и файлы. Не используйте `down -v` или общий Docker
`prune`. Обычный браузер на `/max` не получает подписанный вход MAX; для
проверки на телефоне нужен [бот и личный код](../MAX_DEMO.md).
