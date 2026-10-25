# Изменения

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии и выпуск — [docs/releasing.md](docs/releasing.md). Каждый PR, который меняет API или поведение библиотеки, добавляет строку в «Unreleased»; при выпуске раздел получает номер версии и дату.

## [Unreleased]

### Изменено

- FIN-243: независимые statements-профили unit/integration/API и обязательные пороги80 с готовым go-test-coverage v2.20.1; guards пустых/пропущенных наборов, исполнение Example/Fuzz и проверки публичного Go API. Только тестовый пакет internal/pgtest исключён из одинакового знаменателя всех трёх уровней; его тесты продолжают выполняться. Runtime и экспортируемый API не менялись.
