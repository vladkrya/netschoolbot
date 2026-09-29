# NetSchool iOS 2.0.3: мобильное API (Респ. Мордовия)

Реализация: `netschoolpy.MobileNetSchool` (netschoolpy 9.0.0). В боте включается командой `/changeapi`.

Снято 29.09.2026 через Charles 5.2.1 с iPhone (приложение NetSchool 2.0.3, build 231), регион «Респ. Мордовия», сервер `sgo.e-mordovia.ru`, учётка ученика. Все пути ниже реально прошли через прокси, кроме двух маршрутов скачивания файлов: их нашли подбором и проверили `curl` с тем же токеном (помечены *проверено вручную*).

Идентификаторы в примерах: `userId = studentId = 100001`, `schoolId = orgId = 1110`, `schoolYearId = 7131`.

## Хосты

| Хост | Роль |
| --- | --- |
| `mobile.ir-tech.ru` | Каталог регионов, серверное время, регистрация push. Без авторизации (кроме push). |
| `identity.ir-tech.ru` | OIDC-провайдер ИРТех: выдаёт Bearer-токен мобильного клиента. |
| `sgo.e-mordovia.ru` | Региональный «Сетевой город». Вход через `/webapi/*` (cookie-сессия), данные через `/api/mobile/*` (Bearer). |

Для других регионов базовый URL берётся из каталога (поле `url`), а путь `/api/mobile/*` у всех одинаковый.

## Общие правила запросов к `/api/mobile/*`

- Заголовок `Authorization: Bearer <access_token от identity.ir-tech.ru>`. Проверено: **cookie не нужны**, одного Bearer достаточно.
- Приложение добавляет к каждому запросу `lng=ru&appVersion=2.0.3`. Сервер принимает и без них.
- User-Agent приложения: `NetSchool/231 CFNetwork/3896.200.41 Darwin/27.2.0`.
- Массивы передаются повторением ключа: `assignmentId=1&assignmentId=2`.
- Даты в query имеют вид `YYYY-MM-DD`, в ответах ISO 8601 (`2026-09-21T08:30:00+03:00`, у «дней» без зоны).

## Авторизация (логин и пароль)

Вход проходит в WebView на сайте региона, а потом одноразовый код меняется на долгоживущий токен ИРТех.

### 1. Веб-вход в «Сетевой город» (cookie-сессия)

Все запросы идут на `https://sgo.e-mordovia.ru`, cookie сохраняются между шагами.

| # | Запрос | Что отдаёт |
| --- | --- | --- |
| 1 | `GET /login?mobile&appVer=2.0.3` | HTML формы входа, cookie `ESRNSec` |
| 2 | `GET /webapi/auth/context` | `{now, productName, version, serverTimeZone, environment, …}` |
| 3 | `GET /webapi/logindata` | Возможности входа: `{schoolLogin, esiaLogin, esiaLoginPage:"/webapi/sso/esia/crosslogin", version:"5.56.0", …}` |
| 4 | `POST /webapi/auth/login-state` (form: `mobile=1`) | Строка-GUID `loginState` и cookie `NSLOGINSTATEID` |
| 5 | `GET /webapi/settings/preloginnotice` | Баннер до входа `{enablePopUp, popUpDisplayText, …}` |
| 6 | `POST /webapi/auth/getdata` (пустое тело) | `{lt, ver, salt}` и cookie `NSSESSIONID` |
| 7 | `POST /webapi/auth/login` (form, см. ниже) | `{at, accessToken, refreshToken, timeOut:3600000, accountInfo:{…}, entryPoint}`, cookie `ESRNSec`, `TTSLogin` |
| 8 | `POST /app/simple/mobile` (form: `at=<at>`) | HTML мобильной оболочки |
| 9 | `GET /webapi/v2/settings?at=<at>&cacheEvictKey=<n>` (заголовок `at: <at>`) | Словарь флагов `{"0": serverId, "1": baseUrl, "201": bool, …}` |
| 10 | `GET /webapi/appsettings/mobileDeepLink` | `irtech://mobile-app` |
| 11 | `GET /webapi/context` | `{at, userId, schoolId, schoolYearId, globalYearId, serverId, roles, rights, schoolyear, menuContext, …}` |
| 12 | `GET /webapi/auth/mobile/info` | `{isMobile:true, mobileBind:false}` |
| 13 | **`GET /webapi/mysettings/mobile/pincode`** | **`{subjectId, userCode:"123456"}`**: одноразовый код для шага 2 |
| 14 | `GET /webapi/mysettings/mobile/consent` | `true`/`false`: принято ли согласие на обработку ПДн |
| 15 | `POST /webapi/auth/logout` | Закрывает веб-сессию: она больше не нужна |

После шага 7 веб-запросы несут заголовок `at: <at>`, а шагам 7–8 нужен `Referer: https://sgo.e-mordovia.ru/authorize/login?mobile&appVer=2.0.3`.

Тело `POST /webapi/auth/login` (`application/x-www-form-urlencoded`):

```
loginState=<из шага 4>
loginType=1
lt=<из getdata>
ver=<из getdata>
scid=1110            # id школы
un=<логин>
pw2=<хеш пароля, 32 hex>
mobile=1
```

`pw2` в захвате имеет длину 32 (hex MD5), поля `pw` нет. По известной реализации (netschoolapi):
`pw2 = md5(salt + md5(password.encode("windows-1251")).hexdigest()).hexdigest()`. *Формулу из трафика не проверить: для этого нужен пароль.*

Если на шаге 7 пришёл ответ 409, веб-сессия уже активна. В одной из прошлых записей такое было, и клиент повторял вход.

### 2. Обмен кода на токен ИРТех

```
POST https://identity.ir-tech.ru/connect/token
Content-Type: application/x-www-form-urlencoded

grant_type=urn:ietf:params:oauth:grant-type:device_code
device_code=<userCode из /webapi/mysettings/mobile/pincode>
client_id=parent-mobile
client_secret=<статический секрет приложения, 36 символов>
appVersion=2.0.3
```

Ответ:

```json
{"access_token":"<JWT RS256>","refresh_token":"<opaque>","id_token":"<JWT>",
 "expires_in":5184000,"token_type":"Bearer","scope":"openid offline_access"}
```

- `access_token` живёт **60 дней** (`expires_in` = 5 184 000 с). Claims: `iss=https://identity.ir-tech.ru`, `client_id=parent-mobile`, `sub=<subjectId из pincode>`.
- Этот же токен принимают `sgo.e-mordovia.ru/api/mobile/*`, `identity.ir-tech.ru/users/*` и `mobile.ir-tech.ru/.../register-device`.
- `client_secret` в репозиторий не кладём: его значение есть в HAR-захвате (см. конец файла), хранить в `.env`.
- Обновление, скорее всего, стандартное OIDC: `grant_type=refresh_token&refresh_token=…&client_id=parent-mobile&client_secret=…`. *Не наблюдалось и не проверялось: вызов сменил бы refresh-токен приложения на телефоне.*

### Срок токена и продление в боте

За 3 дня до конца срока бот пробует продлить токен сам (`MobileNetSchool.refresh()`, `grant_type=refresh_token`) — раз в 6 часов, а не каждый проход. Продление на живом сервере **не проверялось**, поэтому оно считается удавшимся, только если срок реально вырос. Иначе пользователю один раз приходит сообщение «мобильный доступ скоро истечёт, войдите заново: /login» (или `/changeapi`, чтобы вернуться на веб-API). Отметка о предупреждении лежит в `users.token_warned_at`: перезапуск бота её не сбрасывает, а свежий токен снимает.

Ожидаемая цена продления: сервер может аннулировать `refresh_token`, выданный приложению NetSchool на телефоне, — тогда в приложении придётся войти заново. Бот принимает это как приемлемое: без продления он в любом случае потерял бы доступ через 60 дней. Если сервер новый `refresh_token` не выдал, клиент оставляет прежний.

Токен `accessToken` из `/webapi/auth/login` — другой JWT от самого `sgo` (`iss=https://sgo.e-mordovia.ru/`, живёт 1 час) и для мобильного API не используется.

### 3. Привязка к серверу

| Запрос | Ответ |
| --- | --- |
| `GET https://identity.ir-tech.ru/users/endpoints` (Bearer) | `[{serverId:"689ad2d5-…", userId:"100001"}]` |
| `GET https://mobile.ir-tech.ru/api/v1/mobile/parent/end-points/{serverId}` | Описание региона, в том числе `url` = база мобильного API |
| `GET {url}/users?v=2` (Bearer) | Профиль, школы, классы, дети (для родителя) |

## Каталог регионов (`mobile.ir-tech.ru`, без авторизации)

| Метод | Путь | Параметры | Ответ |
| --- | --- | --- | --- |
| GET | `/api/v1/mobile/parent/end-points` | — | `{index, pageSize, totalPages, totalItems, items:[EndPoint]}`, все регионы разом *(проверено вручную)* |
| GET | `/api/v1/mobile/parent/end-points/{serverId}` | — | `EndPoint` |
| GET | `/api/v1/mobile/parent/locations` | — | `{items:[{id, parentId, name, code}]}` *(проверено вручную)* |
| GET | `/api/v1/mobile/parent/languages` | `versionMob` | `[]` |
| GET | `/api/v1/mobile/parent/time` | — | серверное время, число |
| GET | `/api/v1/mobile/parent/endpoint-notifications` | `ServerId`, `Actual=true` | `[]`, баннеры региона |
| POST | `/api/v1/mobile/parent/notifications/register-device` | JSON `{deviceToken:<FCM>, deviceSystem:"iOS", appVersion}`, Bearer | 202, регистрация push |

`EndPoint`: `{id, serverId, locationId, name, description, url, timeout, demo, modulePortfolio, moduleEvents, moduleReferences, moduleSchoolFood, modulePeriodEvents, pushNotifications, nationalCA, unavailable, comment}`. Если `unavailable=true`, регион отключён (например, Челябинская обл. перешла на «Госуслуги Моя школа»). Мордовия: `https://sgo.e-mordovia.ru/api/mobile`.

## Данные: `https://sgo.e-mordovia.ru/api/mobile`

### Профиль и учебный год

| Метод | Путь | Параметры | Ответ |
| --- | --- | --- | --- |
| GET | `/users` | `v=2` | `[{id, firstName, nickName, loginName, isStudent, isParent, isStaff, children, organizations:[{organization:{id,name,isAddSchool}, classes:[{classId,className}], isActive}]}]` |
| GET | `/education` | `studentId` | `[{schoolyear:{id,name,startDate,endDate}, school:{id,name}, class:{id,name,isFree}, isAddSchool}]`, все годы обучения; текущий идёт первым |
| GET | `/terms` | `studentId`, `schoolYearId` | `[{id, name:"1 четверть", type:{id,name}, startDate, endDate, current}]` |
| GET | `/subjects` | `studentId`, `schoolYearId` | `[{id, name, order, federalСurriculum}]` (в ключе кириллическая «С») |

### Расписание и уроки (экраны «День» и «Расписание»)

| Метод | Путь | Параметры | Ответ |
| --- | --- | --- | --- |
| GET | `/classmeetings` | `studentIds` (повторяемый), `startDate`, `endDate` | `[ClassMeeting]`. Приложение берёт окна по 2–3 недели вперёд и назад |
| GET | `/classmeetings/{classmeetingId}` | `studentId` | `ClassMeeting`, экран урока |

`ClassMeeting`: `{classmeetingId, studentId, assignmentId:[int], order, scheduleTimeNumber, scheduleTimeRelay, day, subjectName, subjectId, subjectGroupId, startTime, endTime, teachers:[{id,name}], lessonTheme, roomName, attachmentsExists, resultsExists, attendance, addEducation, extraActivity, distanceMeetingId}`. В `attendance` приходит `null` или код отметки (`"Б"` — болезнь).

### Задания и оценки

| Метод | Путь | Параметры | Ответ |
| --- | --- | --- | --- |
| GET | `/assignments` | `studentId`, `assignmentId` (повторяемый, в захвате до 75 id в одном запросе) | `[Assignment]`, ДЗ и оценки к урокам из `classmeetings[].assignmentId` |
| GET | `/assignments/{assignmentId}` | `studentId` | `Assignment` |
| GET | `/assignments/for-current-term` | `studentId`, `withoutMarks=true`, `withExpiredClassAssign=false`, `onlyHomeWorks=false` | `[Assignment]`, вкладка «Д/З → Домашние задания» |
| GET | `/assignments/{assignmentId}/answer` | `studentId` | `{text, files:[]}`, ответ ученика |
| GET | `/attachments` | `assignmentId` | `[{classMeetingId, assignmentId, attachmentId, fileName, description}]` |
| GET | `/attachments/{attachmentId}` | — | **файл** (бинарный, правильный `Content-Type`) *(проверено вручную)* |
| GET | `/period-events` | `studentId`, `limit=100`, `offset=0`, `SubjectGroupId` (повторяемый, все группы ученика), `PeriodDays` (`1` = сутки, `14`) | `{events:[{type, result?, homework?}], periodFrom, periodTo, serverTime}`, лента «Д/З → События» |

`Assignment`: `{assignmentId, classmeetingId, assignmentName, description, result (оценка, float|null), resultDate, comment, classAssignment, duty (долг), weight, assignmentTypeId, assignmentTypeAbbr, assignmentTypeName, subjectId, subjectName, assignmentDate, dueDate, attachmentsExists, hasTextAnswer, hasFileAnswers, answerFilesCount, canAnswer, extraActivity}`.

**Время публикации задания** берётся из `assignmentDate`. Надпись «опубликовано 27.08 в 02:05» на экране ДЗ — это `assignmentDate: "2026-08-27T02:05:45+03:00"` того же задания. Поле приходит в `/assignments`, `/assignments/{id}` и `/assignments/for-current-term` (с поясом), а также в `homework` событий `/period-events` (без пояса). `dueDate` — срок сдачи (начало урока), `resultDate` — когда поставлена оценка. Веб-клиент `NetSchool` его не разбирает (есть ли поле в сыром веб-ответе, не проверялось); в netschoolpy 9.0 оно доступно как `Assignment.published_at` у `MobileNetSchool` и через `netschoolpy.published_at(raw)`.

Типы заданий из захвата: `3 ДЗ` (домашнее задание), `4 К` (контрольная), `5 С` (самостоятельная), `8 Д` (диктант), `10 О` (ответ на уроке).

События `period-events`: `type = "ResultInfo"` → поле `result:{subjectId, subjectName, date, assignmentId, result, assignmentTypeId/Abbr/Name, resultDate}`; `type = "HomeworkInfo"` → поле `homework:{assignmentId, subjectId, subjectName, date, assignmentDate, attachmentsExists, assignmentTypeId/Abbr/Name}`.

### Итоги

| Метод | Путь | Параметры | Ответ |
| --- | --- | --- | --- |
| GET | `/totals` | `studentId`, `schoolYearId` | `[{subjectId, termTotals:[{term:{id,name}, markCode, avgMark}], yearTotals:[{period:{id, periodName:"Годовая"/"Итоговая", periodType:"Year"/"Total"}, markCode}]}]` |
| GET | `/analytics/subject-performance` | `studentId`, `subjectId`, `termId` | `{subject, term, averageMark, classAverageMark, maxMark, classmeetingsStats:{passed,scheduled}, teachers, results:[{date, assignmentId, classMeetingId, classMeetingDate, result, weight, duty, comment, assignmentType…}], markStats:[{mark,count,fraction}], attendance:[{classMeetingId, classMeetingDate, attendanceMark}]}`, экран «Итоги по предмету» |

### Почта

| Метод | Путь | Параметры | Ответ |
| --- | --- | --- | --- |
| GET | `/mail/messages` | `userId`, `boxType` = `INBOX` / `SENT` / `DRAFT` (вкладка «Удалённые» не открывалась), `page`, `pageSize=27`, `expand=text&expand=subject&expand=author`, `OrderInfo.Field=Sent`, `OrderInfo.Ascending=false` | `{page, pageSize, totalPages, totalItems, items:[{id, subject, text, sent, author:{id,name}, notify, read, noReply, mailBox, fileAttachments}]}` |
| GET | `/mail/messages/unread-count` | `userId` | число |
| POST | `/mail/messages/{messageId}/read` | `userId`, пустое тело | Полное письмо `{id, subject, text, sent, author, to:[Recipient], toNames, cc, bcc, fileAttachments:[], mailBox, read, canReplyAll, canForward, canEdit, noReply}`. **Помечает письмо прочитанным** |
| GET | `/mail/blocked-users` | `userId` | `[]` |
| GET | `/address-book` | `userId`, `orgId` | `[{groupName, groupType, groupRecipientId, recipients:[Recipient], subGroups:[{groupRecipientId, groupName, groupType, recipients:[Recipient+tags]}]}]`, адресная книга (учителя, ученики, родители, администрация, завучи) |

`Recipient`: `{id (str), name, organizationName, isGroupRecipient, subRecipients}`.

### Объявления, push, портфолио

| Метод | Путь | Параметры | Ответ |
| --- | --- | --- | --- |
| GET | `/announcements` | `studentId` | `[{announcementId, postDate, title, description (HTML), author:{id,name}, recipients:[str], attachments:[], organizations:[{id,name}]}]` |
| GET | `/v2/push-notifications/settings` | — | `[{code, name, enabled}]`, коды `Homework`, `Result`, `TermTotal`, `YearTotal` |
| GET | `/portfolios` | — | `[{id, name, userId, portfolioType, users:[{id,name}], groups:[{id,name}]}]` |
| GET | `/portfolios/{portfolioId}` | — | `{id, userId, portfolioType, groups:[{id, name, order, parentGroupId, responsibleUserId, isReadOnly, groups, links, docs:[{id, name, fileName}] \| null, accessType}]}` |
| GET | `/portfolios/{portfolioId}/docs/{docId}` | — | **файл** документа портфолио *(проверено вручную)* |

## Что делает приложение по экранам

1. **Запуск:** `parent/languages`, `parent/time`, `parent/endpoint-notifications`.
2. **Выбор региона:** каталог `end-points` (сначала список, потом детали по каждому `serverId`).
3. **Вход:** шаги 1–15 выше, затем `connect/token`, `users/endpoints`, `end-points/{serverId}`, `/users?v=2`, `/announcements`, `register-device`, `/v2/push-notifications/settings`.
4. **«День»:** `/classmeetings` (окно ±2 недели), затем `/assignments` пачками; в карточке урока `/classmeetings/{id}`; в ДЗ `/assignments/{id}`, `/attachments?assignmentId=`, `/assignments/{id}/answer`.
5. **«Расписание»:** `/classmeetings` соседних недель и `/assignments`.
6. **«Д/З»:** `/assignments/for-current-term`, `/period-events` (`PeriodDays=14`, после фильтра «за последние сутки» `PeriodDays=1`).
7. **«Итоги»:** `/education` → `/terms`, `/subjects`, `/totals`; предмет → `/analytics/subject-performance`.
8. **«Сообщения»:** `/mail/messages?boxType=…`, `/mail/messages/unread-count` (после каждого действия), `/mail/messages/{id}/read`, при составлении письма `/mail/blocked-users` и `/address-book`.
9. **Меню → Портфолио:** `/portfolios`, `/portfolios/{id}`.

## Вход через Госуслуги и мобильный режим

Проверено 29.09.2026 на живой учётке: вход по QR → `NetSchool.mobile_token()` из ЕСИА-сессии выдал код, `identity.ir-tech.ru` обменял его на 60-дневный токен с `refresh_token`, и `/api/mobile/*` его принял (`users`, `classmeetings`). Шаг `POST /app/simple/mobile`, который делает приложение, не понадобился.

Поэтому пользователям Госуслуг доступен мобильный режим (`/changeapi`), и ESIA-сессию держать больше не нужно. Как устроена защита от цикла «войдите заново»:

- Токен берётся из живой веб-сессии: при переключении (`SessionPool.switch_api` → `MobileNetSchool.from_web`) и сразу после входа через Госуслуги (`_finish_login` → `_settle_api_mode`). Пока человек в мобильном режиме, новый вход по коду подтверждения превращается в токен тут же — позже без кода этого не сделать.
- Не вышло — `api_mode` возвращается на `web`, и пользователь узнаёт об этом в сообщении о входе. Прежний цикл (мобильный режим без токена и без способа его получить) невозможен.
- По истечении токена (60 дней; раньше — автопродление, см. «Срок токена») человек входит через Госуслуги заново, и тот же `_finish_login` выдаёт новый токен. Пока он не вошёл, пул отвечает «Токен мобильного API истёк. Войдите заново: /login».

Ограничение проверки: это один успешный прогон на одной учётке (Мордовия); на других регионах и при нескольких организациях в аккаунте не пробовалось.

## Что не наблюдалось

- Отправка письма, сохранение черновика, удаление письма, ответ на задание (текст или файл), изменение настроек push: действия с записью намеренно не выполнялись.
- Вход через Госуслуги (ЕСИА): `logindata.esiaLoginPage = /webapi/sso/esia/crosslogin`, поток не проходился.
- Обновление токена (`grant_type=refresh_token`), см. выше.
- Родительский аккаунт: у родителя `/users?v=2` возвращает `children`, и `studentId` берётся оттуда (по устройству API; не проверялось).

## Исходный захват

HAR-файл сеанса: `~/Documents/netschool-mobile-2026-09-29.har`. Он **вне репозитория**, потому что содержит живой 60-дневный Bearer-токен, хеш пароля и `client_secret`. Хранить только локально и не коммитить.
