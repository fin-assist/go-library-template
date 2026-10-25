# Изменения

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии и выпуск — [docs/releasing.md](docs/releasing.md). Каждый PR, который меняет API или поведение библиотеки, добавляет строку в «Unreleased»; при выпуске раздел получает номер версии и дату.

## [Unreleased]

### Изменено

- FIN-310: `internal/pgtest` больше не копирует go-migrate: оставлен только выбор тестового PostgreSQL (`TEST_DATABASE_URL`/testcontainers, skip локально, ошибка в CI), создание БД, правила файлов и обход up → down → up делегированы `github.com/fin-assist/go-migrate` v0.1.1 (`migratetest`, `migrate.Validate`). go-migrate — зависимость только тестов, прямая `golang-migrate` ушла в indirect. Исключение `internal/pgtest` из покрытия не изменилось; runtime и экспортируемый API не менялись.
- FIN-247: только исходный пустой go-library-template сообщает N/A для coverage после обязательного успешного выполнения всех трёх наборов. При появлении production statements автоматически действуют прежние total80; новые библиотеки не наследуют исключение. Проверки suites, required go, защиты и Controller Renovate сохранены; runtime и публичный API не менялись.
- FIN-243: независимые statements-профили unit/integration/API и обязательные пороги80 с готовым go-test-coverage v2.20.1; guards пустых/пропущенных наборов, исполнение Example/Fuzz и проверки публичного Go API. Только тестовый пакет internal/pgtest исключён из одинакового знаменателя всех трёх уровней; его тесты продолжают выполняться. Документирован эффект постоянно красного go на Renovate retry/close/quarantine и необходимость отдельного решения об операционной паузе. Runtime и экспортируемый API не менялись.
