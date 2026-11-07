# Сравнительный анализ и классификация моделей балансировки нагрузки в распределённых СУБД

Хлопцев Артём Евгеньевич, ПО-1.

Научная статья о моделях балансировки реплик и управляющих ролей в распределённых СУБД. ПЗ-3.

В работе предложены классификация подходов, модель размещения с ограничениями и воспроизводимое сравнение четырёх стратегий. Синтетический эксперимент включает 192 траектории. Он исследует компромисс между задержкой узловой операции и объёмом копирования данных.

## Структура

| Файл | Назначение |
| --- | --- |
| `article.tex` | Статья |
| `misisarticle.cls` | Класс оформления |
| `.github/workflows/build.yml` | Сборка PDF в GitHub Actions |
| `Makefile` | Локальная сборка и архив для Overleaf |
| `example/` | Пример оформления |
| `experiment/` | Генератор нагрузки, балансировщики, проверки и анализ |
| `experiment/results/` | Конфигурация, исходные журналы и результаты |
| `figures/tradeoff.png` | График, построенный по результатам эксперимента |

## Воспроизведение эксперимента

Python 3.9 или новее, стандартная библиотека:

```sh
python3 -m unittest discover -s experiment -p 'test_*.py' -v
python3 experiment/run.py --out experiment/results
python3 experiment/analyze.py experiment/results
```

Полный протокол, определения метрик и ограничения модели описаны в [experiment/README.md](experiment/README.md).

## Сборка

PDF собирается автоматически после push и pull request. Файлы `article.pdf` и `example.pdf` доступны в разделе Artifacts успешного запуска [GitHub Actions](https://github.com/NemoFlip/distributed-db-load-balancing-article/actions).

Локально необходим Tectonic 0.17.0:

```sh
make article
make example
make dist
```

Результаты: `build/article.pdf`, `example/build/example.pdf` и `dist/misis-article.zip`.

Для Overleaf: загрузить архив из `make dist`, выбрать компилятор XeLaTeX и главный файл `article.tex`.

## Лицензия

Класс `misisarticle.cls` распространяется по LPPL 1.3c. Текст лицензии находится в [LICENSE](LICENSE).
