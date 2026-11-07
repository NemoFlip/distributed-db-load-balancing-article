# Сравнительный анализ и классификация моделей балансировки нагрузки в распределённых СУБД

Хлопцев Артём Евгеньевич, ПО-1.

Материалы научной статьи. ПЗ-3.

## Структура

| Файл | Назначение |
| --- | --- |
| `article.tex` | Статья |
| `misisarticle.cls` | Класс оформления |
| `.github/workflows/build.yml` | Сборка PDF в GitHub Actions |
| `Makefile` | Локальная сборка и архив для Overleaf |
| `example/` | Пример оформления |

## Сборка

PDF собирается автоматически после push и pull request. Файлы `article.pdf` и `example.pdf` доступны в разделе Artifacts успешного запуска [GitHub Actions](https://github.com/NemoFlip/distributed-db-load-balancing-article/actions).

Локально необходим XeLaTeX или Tectonic:

```sh
make article
make example
make dist
```

Результаты: `build/article.pdf`, `example/build/example.pdf` и `dist/misis-article.zip`.

Для Overleaf: загрузить архив из `make dist`, выбрать компилятор XeLaTeX и главный файл `article.tex`.

## Лицензия

Класс `misisarticle.cls` распространяется по LPPL 1.3c. Текст лицензии находится в [LICENSE](LICENSE).
