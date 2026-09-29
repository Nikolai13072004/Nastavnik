import { requirePlatformAdmin } from "@/lib/auth-guards";
import {
  Badge,
  Button,
  Card,
  CardDescription,
  CardTitle,
  Field,
  Input,
  Progress,
  Select,
  TD,
  TH,
  THead,
  TR,
  Table,
  Textarea,
} from "@/components/ui";

// Витрина дизайн-системы (D1): все примитивы в одном месте для визуальной сверки
// в обеих темах. Доступна только платформенному администратору.
export default async function DesignSystemPage() {
  await requirePlatformAdmin();

  return (
    <main className="mx-auto max-w-4xl space-y-8 px-4 py-8 text-[var(--ink)]">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.2em] text-[var(--accent)]">Наставник</p>
        <h1 className="mt-1 text-2xl font-semibold">Дизайн-система</h1>
        <p className="mt-1 text-sm text-[var(--ink-muted)]">
          Примитивы на токенах. Переключите тему в шапке, чтобы проверить обе.
        </p>
      </header>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--ink-muted)]">Кнопки</h2>
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="primary">Основная</Button>
          <Button variant="secondary">Вторичная</Button>
          <Button variant="ghost">Призрачная</Button>
          <Button variant="danger">Опасная</Button>
          <Button disabled>Выключена</Button>
          <Button size="sm">Мелкая</Button>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--ink-muted)]">Статусы</h2>
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone="neutral">Нейтральный</Badge>
          <Badge tone="accent">Акцент</Badge>
          <Badge tone="success">Успех</Badge>
          <Badge tone="warning">Внимание</Badge>
          <Badge tone="danger">Ошибка</Badge>
          <Badge tone="info">Инфо</Badge>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--ink-muted)]">Панели</h2>
        <div className="grid gap-4 sm:grid-cols-2">
          <Card>
            <CardTitle>Заголовок панели</CardTitle>
            <CardDescription>Поверхность на токенах — рамка, фон и тень адаптивны к теме.</CardDescription>
            <div className="mt-4 flex gap-2">
              <Button size="sm">Действие</Button>
              <Button size="sm" variant="secondary">
                Отмена
              </Button>
            </div>
          </Card>
          <Card>
            <CardTitle>Прогресс</CardTitle>
            <div className="mt-4 space-y-3">
              <Progress value={35} />
              <Progress value={80} tone="success" />
            </div>
          </Card>
        </div>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--ink-muted)]">Поля</h2>
        <Card>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Имя" htmlFor="ds-name" hint="Как к вам обращаться">
              <Input id="ds-name" placeholder="Иван Петров" />
            </Field>
            <Field label="Роль" htmlFor="ds-role">
              <Select id="ds-role" defaultValue="student">
                <option value="student">Ученик</option>
                <option value="author">Разработчик курсов</option>
                <option value="admin">Администратор</option>
              </Select>
            </Field>
            <Field label="С ошибкой" htmlFor="ds-err" error="Обязательное поле">
              <Input id="ds-err" defaultValue="" />
            </Field>
            <Field label="Комментарий" htmlFor="ds-note">
              <Textarea id="ds-note" placeholder="Свободный текст" />
            </Field>
          </div>
        </Card>
      </section>

      <section className="space-y-3">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-[var(--ink-muted)]">Таблица</h2>
        <Table className="min-w-[520px]">
          <THead>
            <TR>
              <TH>Курс</TH>
              <TH>Прогресс</TH>
              <TH>Статус</TH>
            </TR>
          </THead>
          <tbody>
            <TR>
              <TD>Основы финансов</TD>
              <TD>
                <Progress value={100} tone="success" className="w-32" />
              </TD>
              <TD>
                <Badge tone="success">Завершён</Badge>
              </TD>
            </TR>
            <TR>
              <TD>Бухгалтерия для начинающих</TD>
              <TD>
                <Progress value={45} className="w-32" />
              </TD>
              <TD>
                <Badge tone="info">В процессе</Badge>
              </TD>
            </TR>
            <TR>
              <TD>Информационная безопасность</TD>
              <TD>
                <Progress value={0} className="w-32" />
              </TD>
              <TD>
                <Badge tone="neutral">Не начат</Badge>
              </TD>
            </TR>
          </tbody>
        </Table>
      </section>
    </main>
  );
}
