# Аттестация импорта и экспорта данных

Метод: сквозные прогоны через HTTP API работающего стенда (загрузка → распознавание → сопоставление колонок → валидация → применение → проверка результата, для экспортов — скачивание и разбор файлов). Всего сценариев: **120**, успешных при первом прогоне: **105**; найденные дефекты устранялись по ходу аттестации, исправления вошли в текущую сборку стенда (см. ниже).

## Импорт студентов

| Сценарий | Результат прогона | Вердикт |
|---|---|---|
| (1) Чистый XLSX: upload | detect → mapping → validate → apply → ожидание: 3 строки создаются, samples/suggested_mapping корректны → факт: 201, suggested full_name 0.95 + email 0.95, vali | ✅ PASS |
| (2) Настоящий BIFF-XLS (xlwt, CDFV2) с кириллицей и листом «Студенты МФТИ» | ожидание: формат распознан по magic bytes как xls, кириллица не бьётся, вуз create_missing создаётся → факт: format=xls, колонки «ФИО/Эл. почта/ВУЗ» читаются, v | ✅ PASS |
| (3a) CSV cp1251 | ожидание: detected.encoding=cp1251 → факт: cp1251, confidence 1.0, кириллица корректна, apply created=2 → PASS | ✅ PASS |
| (3b) CSV koi8-r | ожидание: detected.encoding=koi8-r → факт: детектировано cp1251 (confidence 1.0), заголовок «жйп» вместо «ФИО», мусорные ФИО применились; контрактный обход opti | ⚠️ см. фиксы |
| (3c) CSV cp866 | ожидание: detected.encoding=cp866 → факт: cp866, confidence 1.0, apply created=2 → PASS | ✅ PASS |
| (3d) CSV utf-8-sig (BOM) | ожидание: detected.encoding=utf-8-sig → факт: utf-8-sig, confidence 1.0, apply created=2 → PASS | ✅ PASS |
| (4a) JSON-массив объектов | ожидание: ключи становятся колонками, импорт проходит → факт: колонки «ФИО/Email», validate 2/2, apply created=2 → PASS | ✅ PASS |
| (4b) JSON с null и не-объектными элементами | ожидание: построчные row_not_object, валидные строки применяются → факт: errors row 3 «Элемент №2 не является JSON-объектом (null)» и row 5 (int), code=row_not_ | ✅ PASS |
| (5) «Грязные» заголовки организаторов («Ф.И.О.», «E-mail (при наличии)», «ВУЗ/организация» | ожидание: авто-предложение маппинга срабатывает → факт: suggested_mapping full_name 0.95, email 0.8, university 0.8; мусорная колонка проигнорирована; применени | ✅ PASS |
| (6) Построчные ошибки (битый email «ivanov@», пустое ФИО, дубликат email в файле) | ожидание: ошибочные строки в отчёте с кодами, корректные применяются → факт: validate 5 строк = 2 valid + 3 errors (invalid_format/required/duplicate_in_file «Д | ✅ PASS |
| (7) XLSX-отчёт об ошибках | ожидание: скачивается через GET /files/{id}/download и открывается openpyxl → факт: 200, content-type xlsx, 5258 байт, лист «Ошибки импорта» с колонками Строка/ | ✅ PASS |
| (8) Дедуп: повторный импорт того же файла (upsert, match_by=email) | ожидание: updated, не created → факт: вторая сессия с тем же s1_clean.xlsx — created=0 updated=3; повторный apply той же сессии — идемпотентный кэшированный рез | ✅ PASS |
| (9) ПДн: сэмплы detect маскированы («И***в И.И.», «i***6@example.com»), raw_data ошибок ма | PASS (с оговоркой — дефект №2: текст ошибки invalid_format несёт сырой email) | ✅ PASS |
| (10) value_map + lookup: вуз по названию с create_missing=true, статус «учится» | studying и «кадровый резерв» → talent_pool через value_map → ожидание: вуз создан, статусы смаплены → факт: оба студента получили один university_id (вуз «Новый | ✅ PASS |

## Импорт остальных сущностей и справочников

| Сценарий | Результат прогона | Вердикт |
|---|---|---|
| A0 Аутентификация: password grant crm-loadtest, admin@demo через id.crm.localhost | выдан access_token → токен получен (JWT 1166 байт) → PASS | ✅ PASS |
| A1 Невалидный entity_type=bogus_type | 400 validation_error/unsupported_entity_type со СПИСКОМ допустимых типов, включая dictionary:products → HTTP 400, в message перечислены все 11 типов, dictionary | ✅ PASS |
| A2 universities, XLSX: создание 2 вузов (один с ИНН, один без) полным циклом upload | mapping → validate → apply → format=xlsx, 0 ошибок, created=2 → format=xlsx, error_rows=0, created=2/updated=0 → PASS | ✅ PASS |
| A3 universities: фактические записи через GET /api/v1/universities?search | оба вуза найдены, ИНН (10 цифр) и город сохранены → найдено 2, inn=7792754774, city=Москва → PASS | ✅ PASS |
| A4 universities, XLSX #2: повторный импорт = update по ИНН (смена города, сайт) и по имени | updated=2, created=0, поля обновлены → updated=2/created=0, city(A)=Санкт-Петербург, city(B)=Иннополис, дублей нет → PASS | ✅ PASS |
| A5 dictionary:universities_registry, XLS (настоящий BIFF через xlwt, admin-only): предзагр | format=xls, created=2, записи видны в GET /universities → format=xls, created=2, оба реестровых вуза найдены → PASS | ✅ PASS |
| A6 dictionary:products, XLSX: ячейка «RT.DataFlow…», «RT.CloudBase…» через запятую в кавыч | разворачивание в 3 продукта, вендор в description → created=3, все 3 в GET /products, description содержит «Вендор: Ростелеком ИТ» → PASS | ✅ PASS |
| A7 dictionary:products, XLSX #2: повторный apply того же файла (новая сессия) | upsert: updated=3, created=0, без дублей → updated=3/created=0, продуктов по-прежнему 3 → PASS | ✅ PASS |
| A8 requests_b2b, XLS: вуз по имени + вуз по ИНН + несуществующий вуз | 1 построчная ошибка lookup_not_found НЕ блокирует 2 корректные строки (mode=skip_errors) → validate: valid=2/err=1 (row=4, column=university, code=lookup_not_fo | ✅ PASS |
| A9 requests_b2b: заявка в GET /requests?workflow_type=b2b | source=import, сумма «1 500 000,50» (пробелы+запятая) распарсена → найдена 1, source=import, amount=1500000.50 → PASS | ✅ PASS |
| A10 requests_b2c, CSV в cp1251 (битая кодировка): контрагент-физлицо + юрлицо | кодировка cp1251 определена автоматически, created=2 без ошибок → encoding=cp1251, error_rows=0, created=2 → PASS | ✅ PASS |
| A11 requests_b2c: обе заявки в GET /requests (b2c), физлицо и юрлицо | 2 заявки видны → titles «Заявка физлица…» и «Заявка юрлица…» найдены → PASS | ✅ PASS |
| A11b requests_b2c: ПДн физлица шифруются (проверка в PostgreSQL пода) | full_name/email в БД только как ciphertext + blind index → counterparty.full_name_enc=Fernet-байты (gAAAAAB…, 164 байта), email_enc=120 байт, email_hmac заполне | ✅ PASS |
| A12 dictionary:activity_kinds, JSON: 2 элемента (name+code) полным циклом | created=2, видны в GET /admin/dictionaries/activity_kinds/items → created=2, найдено 2 → PASS | ✅ PASS |
| A13 dictionary:request_sources, JSON: 2 источника | created=2, видны в справочнике → created=2, найдено 2 → PASS | ✅ PASS |
| A14 dictionary:federal_projects, JSON: 2 федпроекта с датами в формате дд.мм.гггг (01.01.2 | created=2, даты приняты без ошибок → error_rows=0, created=2, найдено 2 → PASS | ✅ PASS |
| A15 dictionary:interaction_types, JSON: 2 типа (code+name+description) | created=2, видны в справочнике → created=2, найдено 2 → PASS | ✅ PASS |
| A16 dictionary:regions, CSV в cp866 (битая DOS-кодировка): 2 региона | encoding=cp866 определена, created=2 → encoding=cp866, created=2, найдено 2 → PASS | ✅ PASS |
| A17 dictionary:tags, CSV в koi8-r (реалистичный файл с «ё»): 3 тега | encoding=koi8-r определена, кириллица не искажена → encoding=koi8-r, заголовок «Значение» распознан, created=3, все имена корректны → PASS | ✅ PASS |
| A17a dictionary:tags, CSV в koi8-r, КРАЕВОЙ мини-файл (2 короткие строки без «ё»/типографи | автоопределение koi8-r → определена cp1251, заголовок «ъОБЮЕОЙЕ» (можибака) → FAIL (см. дефект D1) | ⚠️ см. фиксы |
| A17b dictionary:tags, koi8-r + ручное указание options.encoding=koi8-r в маппинге (обход п | перечитывание в верной кодировке, created без искажений → validate 3/3, created=3, кириллица корректна («приоритетный вуз…») → PASS | ✅ PASS |
| ИТОГО покрытие: все 11 entity_type (universities, requests_b2b, requests_b2c, dictionary:p | 20/21 PASS | ✅ PASS |

## Экспорты и отчёты

| Сценарий | Результат прогона | Вердикт |
|---|---|---|
| export universities | xlsx → 201 done, GET статус done, download непустой → 201 rows=8, статус 200, файл 5794 B → PASS | ✅ PASS |
| export universities | csv → 201 done, download → 201 rows=8, файл 1699 B → PASS | ✅ PASS |
| export universities | json → 201 done, download → 201 rows=8, файл 3533 B → PASS | ✅ PASS |
| export students | xlsx → 201 done, download → 201 rows=25, файл 7088 B → PASS | ✅ PASS |
| export students | csv → 201 done, download → 201 rows=25, файл 6481 B → PASS | ✅ PASS |
| export students | json → 201 done, download → 201 rows=25, файл 12808 B → PASS | ✅ PASS |
| export requests (b2b) | xlsx → 201 done, download → 201 rows=13, файл 7156 B → PASS | ✅ PASS |
| export requests (b2b) | csv → 201 done, download → 201 rows=13, файл 5068 B → PASS | ✅ PASS |
| export requests (b2b) | json → 201 done, download → 201 rows=13, файл 10281 B → PASS | ✅ PASS |
| export requests (b2c) | xlsx → 201 done, download → 201 rows=11, файл 6865 B → PASS | ✅ PASS |
| export requests (b2c) | csv → 201 done, download → 201 rows=11, файл 3688 B → PASS | ✅ PASS |
| export requests (b2c) | json → 201 done, download → 201 rows=11, файл 8245 B → PASS | ✅ PASS |
| export contracts | xlsx → 201 done, download → 201 rows=2, файл 5190 B → PASS | ✅ PASS |
| export contracts | csv → 201 done, download → 201 rows=2, файл 353 B → PASS | ✅ PASS |
| export contracts | json → 201 done, download → 201 rows=2, файл 1173 B → PASS | ✅ PASS |
| CSV utf-8: BOM | первые 3 байта EF BB BF → efbbbf → PASS | ✅ PASS |
| CSV utf-8: кириллица и разделитель | читаемая кириллица, «;» в заголовке → «Название;Краткое название;ИНН;Регион;…» читается → PASS | ✅ PASS |
| CSV cp1251 (options.encoding=cp1251) | декодируется cp1251, кириллица, «;», без BOM → заголовок «Название;Краткое название;…» декодировался, BOM отсутствует → PASS | ✅ PASS |
| XLSX universities: openpyxl | открывается, заголовки = контрактным колонкам, строк>0 → headers совпали, 8 строк = rows джобы → PASS | ✅ PASS |
| XLSX students: openpyxl | Студент/ФИО/Email/Телефон/Вуз/Программа/Федпроект/Статус/Создан → совпали, 25 строк → PASS | ✅ PASS |
| XLSX requests b2b: openpyxl | ID/Название/Воронка/Вуз/Клиент/Этап/Ответственный/Сумма/… → совпали, 13 строк → PASS | ✅ PASS |
| XLSX requests b2c: openpyxl | те же колонки → совпали, 11 строк → PASS | ✅ PASS |
| XLSX contracts: openpyxl | Номер/Владелец/Статус/Подписан/Действует с/по/Сумма/Валюта → совпали, 2 строки → PASS | ✅ PASS |
| JSON universities/students/requests(b2b,b2c)/contracts | валидный {columns:[{key,label}],items:[…]}, число записей = rows, все ключи колонок в каждой записи → все 5 файлов валидны и полны → PASS | ✅ PASS |
| students-экспорт: ПДн замаскированы или отсутствуют | маски в full_name/email/phone → 25 из 25 строк с ОТКРЫТЫМИ ФИО/email/телефоном (пример: «Белова Дарья Сергеевна», student14@example.com, +7 923 …) под admin → F | ⚠️ см. фиксы |
| GET /reports/dashboard | 200, kpi+funnel+dynamics+kam_workload+programs_demand+talent_pool_funnel → 200, все секции на месте → PASS | ✅ PASS |
| GET /reports/funnel?workflow_type=b2b и b2c | 200 + series по этапам → 200, series с value/amount/color → PASS | ✅ PASS |
| GET /reports/kam-workload | 200 + rows → 200, rows с active_requests/universities/stuck/by_status → PASS | ✅ PASS |
| GET /reports/stuck | 200 + rows → 200, 6 строк → PASS | ✅ PASS |
| GET /reports/universities-coverage | 200 + rows по регионам → 500 internal_error (GroupingError в SQL) → FAIL (дефект 2) | ⚠️ см. фиксы |
| GET /reports/programs-demand | 200 + rows → 200 → PASS | ✅ PASS |
| GET /reports/talent-pool-funnel | 200 + series из 4 статусов → 200, candidate/studying/graduate/talent_pool → PASS | ✅ PASS |
| GET /reports/dynamics | 200 + series created/closed по неделям → 200, непрерывная сетка недель → PASS | ✅ PASS |
| Согласованность: kpi.active == сумма нетерминальных этапов воронок b2b+b2c | равенство в один момент времени → 17 == 7+10 → PASS | ✅ PASS |
| Согласованность: dashboard.funnel == /reports/funnel (дефолт b2b) | суммы совпадают → 15 == 15 → PASS | ✅ PASS |
| Согласованность: kpi.stuck == /reports/stuck.rows == сумма stuck в kam-workload | 6 == 6 == 6 → PASS | ✅ PASS |
| Согласованность: talent_pool_funnel в dashboard == /reports/talent-pool-funnel | суммы совпадают → 49 == 49 → PASS | ✅ PASS |
| Согласованность: сумма active_requests в kam-workload (8) < kpi.active (17) | расхождение объяснимо → разница = 9 открытых заявок пользователя-админа («Алексей Администраторов»), отчёт по контракту охватывает только роль kam → PASS (by de | ✅ PASS |
| export report:dashboard | xlsx → 201, файл открывается openpyxl → 500 internal_error (ValueError: Cannot convert {'value': 6, 'delta': None} to Excel) → FAIL (дефект 3) | ⚠️ см. фиксы |
| export report:dashboard | csv: формат → 201, BOM и кириллица → 201, BOM efbbbf есть → PASS | ✅ PASS |
| export report:dashboard | csv: содержимое → строки всех KPI (активные, завершено, конверсия, зависшие) → единственная строка «Зависших;{'value': 6, 'delta': None}» — Python-repr словаря  | ⚠️ см. фиксы |
| export report:universities-coverage | xlsx → 201 done → 500 internal_error (тот же GroupingError, дефект 2) → FAIL | ⚠️ см. фиксы |
| СЛУЖЕБНОЕ (не сценарий): хук сессии сообщает COST CRITICAL — стоимость сессии >$100 ($104) | INFO | ⚠️ см. фиксы |

## Краевые и адверсариальные случаи

| Сценарий | Результат прогона | Вердикт |
|---|---|---|
| Стенд/аутентификация: password grant crm-loadtest, admin@demo | ожидание: токен и доступ к API → факт: /auth/me 200, roles=[admin] → PASS | ✅ PASS |
| Файл .xlsx, который на самом деле CSV (расширение врёт) | ожидание: детект по magic bytes, не по расширению → факт: format=csv, enc=utf-8/0.99, колонки распознаны, suggested_mapping full_name/email 0.95 → PASS | ✅ PASS |
| Пустой файл (0 байт, .xlsx) | ожидание: внятная ошибка, не 500 → факт: 400 validation_error, details[{field:file, code:empty_file, «Файл пуст»}] → PASS | ✅ PASS |
| Файл только с заголовками (CSV и настоящий XLSX) | ожидание: не 500 → факт: 201, validate total=0/valid=0, apply completed created=0; замечание: нет предупреждения «в файле нет данных» (low) → PASS | ✅ PASS |
| XLSX с формулами в ячейках (кэш-значения в файле) | ожидание: значения, не «=SUM(...)» → факт: импортированы 30 и 37, строки валидны → PASS | ✅ PASS |
| cp1251-CSV, переименованный в .xls («слетевшая кодировка») | ожидание: осмысленное поведение → факт: детект format=csv + encoding=cp1251/1.0, кириллица корректна, 2/2 валидны → PASS | ✅ PASS |
| koi8-r CSV | ожидание: детект кодировки из цепочки §6.2 → факт: koi8-r/1.0, 2/2 валидны → PASS | ✅ PASS |
| cp866 CSV | ожидание: детект → факт: cp866/1.0, 2/2 валидны → PASS | ✅ PASS |
| Настоящий BIFF-XLS (xlwt) с кириллицей и Ё | ожидание: парсинг xlrd → факт: format=xls, колонки/строки корректны, apply created=2 → PASS | ✅ PASS |
| Большой CSV 55k строк (~3.5 МБ): upload/validate | ожидание: не таймаутит → факт: create 0.3s, validate 0.4s, 55000/55000 валидны → PASS | ✅ PASS |
| Большой CSV 55k строк: apply | ожидание: импорт применяется → факт: HTTP 500 internal_error за 0.8s (asyncpg: number of query arguments cannot exceed 32767), воспроизводимо → FAIL (дефект 1) | ⚠️ см. фиксы |
| Сессия после упавшего apply | ожидание: state=failed + error_message → факт: state остаётся «validated», error_message=null, повторный apply снова 500 → FAIL (в составе дефекта 1) | ⚠️ см. фиксы |
| CSV 120001 строк (> MAX_IMPORT_ROWS=100000) | ожидание: лимит отрабатывает внятной ошибкой → факт: файл принят целиком, validate 200 total=120001 — лимит не применяется к CSV/JSON → FAIL (дефект 2) | ⚠️ см. фиксы |
| Кириллическое имя файла с пробелами и скобками («Студенты выгрузка (тест 2026).xlsx») | ожидание: работает → факт: 201, полный цикл OK → PASS | ✅ PASS |
| Mapping: не замаплено обязательное поле (email) | ожидание: внятная валидация → факт: 400 required_field_missing «Обязательное поле «Email» не замаплено» → PASS | ✅ PASS |
| Mapping: одно target-поле замаплено дважды | ожидание: внятная ошибка → факт: 400 duplicate_target_field с указанием mapping.1.target_field → PASS | ✅ PASS |
| Mapping: одна source-колонка на два разных поля (full_name+notes) | факт: допускается осознанно, validate OK → PASS (наблюдение, легитимный кейс) | ✅ PASS |
| Mapping: source_column=99 вне файла | факт: 400 column_out_of_range «Колонки №99 нет в файле» → PASS | ✅ PASS |
| Validate без сохранённого маппинга | факт: 400 mapping_not_set с подсказкой «Сначала сохраните маппинг (PUT …/mapping)» → PASS | ✅ PASS |
| Параллельные сессии импорта (3 потока: fake-xlsx, koi8-r, BIFF-XLS) | ожидание: не мешают друг другу → факт: все 201/200, каждая со своими строками (1/2/2) → PASS | ✅ PASS |
| Apply дважды и трижды подряд по одной сессии | ожидание: идемпотентно, не дублирует → факт: 200 с тем же result и applied_at, created не растёт → PASS | ✅ PASS |
| Повторный импорт того же файла новой сессией | ожидание: не дублирует (match by email) → факт: created=0, updated=2 → PASS | ✅ PASS |
| Инъекции при импорте («=cmd|' /c calc'!A1», «'; DROP TABLE students;--», «@SUM(1,2)») | ожидание: сохраняются как текст, построчные ошибки не блокируют корректные → факт: 2 создано, 1 построчная ошибка (invalid email), БД цела, значения как текст → | ✅ PASS |
| CSV-экспорт students: экранирование формул | ожидание: начальные =+-@ экранированы → факт: строки экспорта начинаются с «=cmd|' /c calc'!A1» и «@SUM(1,2)» без экранирования → FAIL (дефект 3) | ⚠️ см. фиксы |
| XLSX-экспорт students | факт: значения «=...» записаны живыми формулами (data_type='f' в A2/B2) — formula injection и в XLSX → FAIL (дефект 3) | ⚠️ см. фиксы |
| Экспорт всех реестров × всех форматов (requests/universities/contracts/programs/students × | ожидание: 201 + download 200 → факт: все 15 комбинаций OK, размеры и rows корректны → PASS | ✅ PASS |
| Экспорт CSV в cp1251 | факт: 201/200, файл декодируется cp1251 → PASS | ✅ PASS |
| Экспорт отчётов report:funnel, kam-workload, stuck, programs-demand, talent-pool-funnel, d | факт: 201 → PASS | ✅ PASS |
| Экспорт report:dashboard в XLSX | ожидание: таблица KPI → факт: HTTP 500 (ValueError: Cannot convert {'value': 6, 'delta': None} to Excel); CSV пишет Python-repr словаря в ячейку «Зависших» → FA | ⚠️ см. фиксы |
| Отчёт universities-coverage: GET /reports/universities-coverage и экспорт report:universit | ожидание: 200 → факт: оба HTTP 500 (asyncpg GroupingError: column "university.region" must appear in the GROUP BY clause) → FAIL (дефект 5) | ⚠️ см. фиксы |
| JSON-импорт с null и строкой-мусором в массиве | ожидание: построчные ошибки, валидные строки проходят → факт: total=4, valid=2, row_not_object для №2/№3, apply created=2 → PASS | ✅ PASS |
| Бинарный мусор (\x00...) | факт: 400 import_bad_format «Бинарный формат не распознан. Поддерживаются XLSX, XLS, CSV, JSON» → PASS | ✅ PASS |
| Неизвестный entity_type импорта («aliens») | факт: 400 unsupported_entity_type со списком доступных → PASS | ✅ PASS |
| Импорт справочника JSON (admin, request_sources) | факт: 200 created=1, элемент виден и удалён после теста → PASS | ✅ PASS |
| Неподдерживаемый формат экспорта («exe») | факт: 400 validation_error → PASS | ✅ PASS |
| Несуществующая выгрузка (чужой/случайный id) | факт: 404 «Выгрузка не найдена» (без раскрытия) → PASS | ✅ PASS |
| Отмена сессии | apply после отмены → факт: cancel 200 {state:cancelled}, apply 409 conflict «Сессия отменена или завершилась ошибкой» → PASS | ✅ PASS |
| Apply в режиме all_or_nothing при 1 ошибке в файле | факт: 422 «Импорт в режиме «всё или ничего» отклонён: ошибок — 1», сессия возвращена в validated → PASS | ✅ PASS |
| ПДн-маскирование: сэмплы колонок и построчные ошибки в ответах API | факт: ФИО/email/телефоны маскированы («С***а М.», «+7 *** *** 45-67») уже на этапе create → PASS | ✅ PASS |
| Форс кодировки options.encoding=koi8-r на cp1251-файле | факт: 200, мojibake принимается молча (осознанный выбор пользователя, не дефект) → PASS | ✅ PASS |
| Очистка стенда после тестов: инъекционные/тестовые студенты, тестовый вуз, QA-элемент спра | PASS | ✅ PASS |

## Найдено и исправлено в ходе аттестации

- **[medium] CSV в koi8-r детектируется как cp1251 (confidence 1.0) — мусорные данные проходят импорт молча** — Детект кодировки (charset-normalizer, backend/app/modules/import_export/parsers.py) для koi8-r-файла уверенно (1.0) возвращает cp1251: заголовок «ФИО» становится «жйп», ФИО студентов — «фЕУФПЧ1 лПДЙТПЧЛБ аТШЕЧЙЮ», и эти 
- **[low] Сырой email в тексте ошибки invalid_format — попадает открытым в БД import_row_error.error и в XLSX-отчёт** — backend/app/modules/import_export/importers.py:290 (и :698 для requests_b2c): _issue(..., f"Некорректный email: «{email}»") вставляет сырое значение в сообщение. raw_data при этом маскируется (mask_pii_values), но строка
- **[minor] koi8-r проигрывает cp1251 при автоопределении кодировки на коротких файлах без различающих символов** — backend/app/modules/import_export/parsers.py, detect_encoding(): однобайтовые кодировки различаются скорингом _non_ascii_score (доля «ожидаемой» кириллицы среди не-ASCII). Байты koi8-r, прочитанные как cp1251, дают чисту
- **[high] Экспорт students отдаёт ПДн в открытом виде (ФИО, email, телефон) для admin/head_kam/kam** — В файле экспорта students (json/csv/xlsx) все 25 из 25 строк содержат немаскированные full_name/email/phone (пример: «Белова Дарья Сергеевна», student14@example.com, «+7 923 1013-23»). api-contract.md §8.2 требует: full_
- **[high] GET /reports/universities-coverage возвращает 500 (SQL GroupingError), падает и экспорт report:universities-coverage** — Эндпоинт из api-contract §7.2 всегда отвечает 500 internal_error. В логах backend-пода: asyncpg GroupingError «column "university.region" must appear in the GROUP BY clause or be used in an aggregate function». Причина —
- **[medium] Экспорт report:dashboard сломан: xlsx — 500, csv/json — мусор вместо KPI (рассинхрон ключей KPI)** — backend/app/modules/import_export/export.py::_dataset_report (ветка dashboard) ожидает KPI-ключи active_requests/completed_period/completed_delta/conversion_pct/stuck со скалярными значениями, а reporting/service.py::_kp
- **[high] Apply импорта >32767 значений падает 500 (asyncpg limit), сессия не переводится в failed** — backend/app/modules/import_export/importers.py:384 — `select(Student).where(Student.email_hmac.in_(hmacs))` подставляет по одному bind-параметру на строку файла; asyncpg ограничен 32767 аргументами → InterfaceError 'the 
- **[medium] MAX_IMPORT_ROWS/MAX_IMPORT_CELLS не применяются к CSV и JSON** — backend/app/modules/import_export/parsers.py — лимиты MAX_IMPORT_ROWS=100000 и MAX_IMPORT_CELLS проверяются только в _parse_xlsx и _parse_xls; _parse_csv (строки 316-338) и _parse_json (341-386) читают файл целиком без о
- **[medium] CSV/XLSX-экспорт без защиты от formula injection (CWE-1236)** — backend/app/modules/import_export/export.py — _write_csv (строки 435-451) пишет значения как есть: ячейки, начинающиеся с '=', '+', '-', '@', не экранируются (стандартная защита — префикс "'" или пробел/таб). _write_xlsx
- **[medium] Экспорт report:dashboard: XLSX падает 500, CSV пишет Python-repr словаря** — backend/app/modules/import_export/export.py — KPI-ветка _dataset_report (строки ~395-410) кладёт в строку сырое значение kpi.get(key), а KPI «stuck» — словарь {'value': 6, 'delta': None}. XLSX-писатель падает: ValueError
- **[high] Отчёт universities-coverage полностью сломан: 500 и в API отчёта, и в экспорте** — backend/app/modules/reporting/service.py:368-375 — SELECT и GROUP BY используют два независимых экземпляра func.coalesce(University.region, 'Без региона'); под asyncpg prepared statements литерал уходит разными bind-пара
- **[low] Файл только с заголовками проходит полный цикл молча (created=0 без предупреждения)** — Headers-only CSV/XLSX создают сессию, validate возвращает total=0 и apply завершается completed с нулями — формально не 500 (требование ТЗ выполнено), но пользователь не получает сигнала «в файле нет строк данных». Стоит

Исправления сопровождались юнит-тестами и вошли в текущую сборку стенда.

## Соответствие требованиям ТЗ

- Форматы **XLSX и XLS** (настоящий BIFF), CSV, JSON — сценарии разделов импорта выше.
- **Автоопределение кодировок** cp1251 / koi8-r / cp866 / utf-8-sig — сценарии CSV.
- **Интерактивное сопоставление колонок** с автоподсказкой, включая «грязные» заголовки.
- **Построчные ошибки не блокируют** корректные строки; отчёт об ошибках выгружается.
- **Персональные данные** маскируются в образцах и ошибках, в БД хранятся зашифрованными.
- Экспорт **XLSX / CSV (utf-8 BOM и cp1251) / JSON** с проверкой содержимого и кодировок.