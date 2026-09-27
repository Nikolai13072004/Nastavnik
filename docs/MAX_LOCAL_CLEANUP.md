# Что можно убрать с ноутбука

27 сентября 2026 года удалены только перечисленные ниже девять архивов.
Освобождено 2 541 965 312 байт (2,37 ГБ). Другие файлы и Docker не очищались.

## Старые архивы сборок: 2,37 ГБ

Это девять копий Docker-образов, которые использовались для переноса старых
сборок. Архивы не подключены к контейнерам. Они не содержат рабочие тома баз
и не нужны текущему боту на VPS. Их удаление не меняет исходники и результаты
обучения. Пропадут именно эти архивные копии старых сборок.

Удалены эти файлы, не папка `tmp`:

```text
C:\Users\Lenovo\Desktop\Active_Projects\effective-business-max\prodigy\tmp\prodigy-max-pilot-20260924-completion.tar
C:\Users\Lenovo\Desktop\Active_Projects\effective-business-max\prodigy\tmp\prodigy-max-pilot-20260924-employees.tar
C:\Users\Lenovo\Desktop\Active_Projects\effective-business-max\prodigy\tmp\prodigy-max-pilot-20260924-max-auth-cleanup.tar
C:\Users\Lenovo\Desktop\Active_Projects\effective-business-max\prodigy\tmp\prodigy-max-pilot-course-fix.tar
C:\Users\Lenovo\Desktop\Active_Projects\effective-business-max\prodigy\tmp\prodigy-max-pilot-learning-final.tar
C:\Users\Lenovo\Desktop\Active_Projects\effective-business-max\prodigy\tmp\prodigy-max-pilot-learning.tar
C:\Users\Lenovo\Desktop\Active_Projects\effective-business-max\prodigy\tmp\prodigy-max-pilot-quiz.tar
C:\Users\Lenovo\Desktop\Active_Projects\effective-business-max\prodigy\tmp\prodigy-max-pilot.tar
C:\Users\Lenovo\Desktop\Active_Projects\effective-business-max\prodigy\tmp\prodigy-max-search-20260923.tar
```

Удаление окончательное, не через Корзину. Именно эти архивные копии из
Корзины восстановить нельзя. Код, рабочие образы, базы и результаты сохранены.

## Что оставляем

Папки, которые снова понадобятся и создадутся заново, не очищаем:
`.next`, `node_modules`, кеши Docker и моделей. Даже если их можно
восстановить, это приведёт к повторной сборке или загрузке, а не устранит
причину роста. Удаляем только устаревшие копии, которые больше не используются.

## Не удалять вручную

- `src`, `prisma`, `scripts`, `docs`, `deploy`, `.git`: код, миграции и история.
- `.env*`, сертификаты, `data`, `uploads`, резервные копии: настройки и данные.
- `output/playwright`: здесь ещё используются скрипты проверки и подключения.
- `.next`, `node_modules`, кеши сборки и моделей: нужны для дальнейшей работы.
- Docker-тома PostgreSQL, документов и моделей: здесь могут быть данные, которых
  больше нигде нет. Даже остановленный контейнер не означает ненужную базу.
- `C:\Users\Lenovo\AppData\Local\Docker\wsl\disk\docker_data.vhdx`:
  общий диск Docker, около 206 ГБ. В нём находятся несколько проектов,
  не только MAX. Удаление этого файла уничтожит их локальные данные.

До удаления архивов папка `effective-business-max` занимала около 4 ГБ. Большую очистку нужно
делать отдельно: проверить принадлежность старых Docker-образов,
сохранить текущую и предыдущую рабочую версии. Глобальный prune и удаление
общего виртуального диска не применять. Точное освобождение места Windows
после очистки Docker заранее не гарантируется.
