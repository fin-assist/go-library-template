# go-library-template

Шаблон репозитория Go-библиотеки «Понятных финансов» ([FIN-118](https://finassist.youtrack.cloud/issue/FIN-118)). Из него создаются `go-idempotency` ([FIN-78](https://finassist.youtrack.cloud/issue/FIN-78)), `go-outbox` ([FIN-109](https://finassist.youtrack.cloud/issue/FIN-109)), `go-tombstone` ([FIN-110](https://finassist.youtrack.cloud/issue/FIN-110)), `go-identity` ([FIN-119](https://finassist.youtrack.cloud/issue/FIN-119)) и следующие. Сервисы — из [`fin-assist/go-service-template`](https://github.com/fin-assist/go-service-template), контракты и `finerr` — в [`fin-assist/contracts`](https://github.com/fin-assist/contracts).

- [docs/releasing.md](docs/releasing.md) — версии, что несовместимо, CHANGELOG, выпуск, миграции, Renovate.
- [CHANGELOG.md](CHANGELOG.md) — изменения по версиям.
- [AGENTS.md](AGENTS.md) — карта и правила для агентов, Code Review Rules.

## Новая библиотека из шаблона

1. Создать репозиторий `fin-assist/go-<назначение>` из шаблона (GitHub → «Use this template») или влить шаблон в уже созданный.
2. Заменить модуль и пакет: `go mod edit -module github.com/fin-assist/go-<назначение>`, пути импорта (`grep -rl go-library-template | xargs sed -i 's#fin-assist/go-library-template#fin-assist/go-<назначение>#g'`), `package library` → `package <назначение>` в корневых файлах, `MigrationsName` в `migrations.go`.
3. Библиотека без таблиц удаляет `migrations.go`, `migrations_test.go` и каталог `migrations/`; без gRPC — ничего не добавляет.
4. Если библиотека строит ошибки для клиентов — `go get github.com/fin-assist/contracts/gen/go@<версия>` и конструкторы `finerr`.
5. Переписать первый абзац этого README, раздел «Внедрение» ниже и первый абзац `AGENTS.md`.
6. Секреты репозитория: `CLAUDE_CODE_OAUTH_TOKEN` (ревью `@claude`), `GO_MODULES_TOKEN` при приватных модулях. Claude GitHub App, Codex и Renovate стоят на организации.
7. Первый выпуск — `v0.1.0` по [docs/releasing.md](docs/releasing.md).

## Раскладка

```
doc.go               package documentation: what the library does and what it needs from the service
migrations.go        Migrations, MigrationsName, MigrationsTable for the service's migration runner
migrations/          SQL migrations of the library, embedded (NNN_name.up.sql / .down.sql)
internal/pgtest/     PostgreSQL for tests and the up -> down -> up check, same rules as the service runner
scripts/             coverage of changed lines, CHANGELOG checks (Python, standard library)
docs/releasing.md    versions and releases
```

Код библиотеки — в корневом пакете (импорт `github.com/fin-assist/go-outbox` даёт `outbox.Publish`); подпакеты — когда у частей разные потребители (`identity/grpcserver` и `identity/httpmw`), внутренние детали — в `internal/`.

## Внедрение

Раздел, который библиотека переписывает под себя: всё, что сервису нужно сделать, чтобы её подключить, по шагам и с кодом. Общий порядок:

1. `go get github.com/fin-assist/go-<назначение>@vX.Y.Z`.
2. Миграции — источник в `MigrationSources()` (`internal/app/wire.go` сервиса), до источника сервиса:

   ```go
   {Name: outbox.MigrationsName, Table: outbox.MigrationsTable, FS: outbox.Migrations},
   ```

3. Сборка в `wire()`: конструктор библиотеки получает от сервиса всё, что ей нужно, — транзакцию через интерфейс `Tx(ctx) (pgx.Tx, bool)` (его реализует `postgres.DB` шаблона), `*slog.Logger`, `prometheus.Registerer`, часы, настройки. Своих глобальных объектов и переменных окружения у библиотеки нет.
4. Интерцепторы, задания (`worker.Job`), обработчики — куда и в каком порядке.
5. Чек-лист для нового метода, таблицы или эндпоинта сервиса, если библиотека его требует.
6. Метрики и алерты, которые появляются у сервиса.

## Проверки

CI (`.github/workflows/ci.yml`):

| Job | Что |
|---|---|
| `go` | `go mod tidy` без изменений, `go vet`, `go test -race` с PostgreSQL 18 в Docker (testcontainers), включая «вверх → вниз → вверх» для миграций; тесты скриптов; формат `CHANGELOG.md`; покрытие изменённых строк — в сводке прогона и комментарием в PR |
| `lint` | golangci-lint (`.golangci.yml`): `forbidigo` против обхода `finerr` и против глобальных логгера, реестра метрик и переменных окружения; `depguard` против фреймворков |
| `api` | `gorelease`: изменения экспортируемого API против последнего тега и предлагаемая версия; на PR выпуска — проверка номера из `CHANGELOG.md` |
| `migrations` (PR) | существующие миграции не изменены, squawk по новым |

Тег `vX.Y.Z` запускает `.github/workflows/release.yml`: проверка тега и GitHub Release с текстом из `CHANGELOG.md`.

Локально:

```bash
make test          # Docker for testcontainers, or TEST_DATABASE_URL=postgres://... to use your PostgreSQL
make lint
make cover         # cover.out and the table of changed lines against origin/main
make api           # API changes against the latest tag
make release-check VERSION=v0.2.0
```

Цель покрытия — не ниже **80 %** изменённых исполняемых строк production-кода; CI не падает, порог проверяется на ревью (AGENTS.md, Tests). Модули `github.com/fin-assist/*` берутся из git, минуя прокси (`GOPRIVATE`, задан в `Makefile` и CI).

## Автоматизация обновлений зависимостей

[Политика Renovate](docs/renovate-policy.md) (FIN-211): одна актуальная release/main, автомерж полностью ботских PR после обязательного CI, один повтор красного CI и закрытие с явным повтором через Dependency Dashboard. AndroidX/Compose остаются на ручной проверке. Исходные группировки, ограничения версий и расписания — `renovate-base.json`; generated target и quarantine — ветка `renovate-policy`. Активация требует отдельного токена и строгой защиты веток; по умолчанию выключена.
