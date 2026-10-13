# es.json judgment calls (check at 360px)

## Over the README max by 1 char (no natural Spanish fits; please verify the button rows)
- `card.hint` "Pista" (5/4). Fallback if it breaks: "Tip".
- `card.hide_hint` "Quita" (5/4). Fallback: "Ya".
- `card.next` "Sigue" (5/4). Fallback: "Otra". "Siguiente" (9) is too long.
- `common.back` "Atrás" (5/4). No 4-char option.
- `mission.preview` "Lee primero las preguntas" (25/24), wording Picasso asked for. Fallback: "Lee antes las preguntas" (23).

## Squeezes within limits
- `reports.focus` "Próxima semana" (14/15). "Para la próxima semana" (22) and "Enfoque de la semana" (20) don't fit.
- `common.done` "Fin" (4 max, so "Listo" doesn't fit). `common.close` "Salir" ("Cerrar" is 6).
- `card.check` "Checa" (Mexican, 5/5). `home.mix` stays "Mix" ("Mezcla" is 6).
- `mission.read_script` "Ver guion" (11 max). `mission.see_results` "Resultados" (10/11).
- `mission.try_again` "Otra vez" (8/9). `mission.go_questions` "A las preguntas" (15/15).
- `topic.directions` "Cómo llegar" ("Direcciones y lugares" is 21/20).
- `folder.scifi_tech` "Ciencia ficción" (drops "tech").
- `card.your_answer` and `card.your_reply` are both "Tu respuesta".

## Dates
- All locales are es-MX. `date.home_options` changed to weekday/month **short**, which renders "dom 11 de oct". Uppercase it with CSS to get "DOM 11 DE OCT". If you want exactly "DOM 11 OCT" with no "de", that needs formatToParts in code.
- `date.short_options` unchanged, renders "11 oct". `date.batch_options` unchanged (weekday long, "lunes").
- `date.with_year` "{date} de {year}" gives "11 oct de 2026". `date.range` unchanged: "{start} – {end}".
- `home.new_batch` "Tarjetas nuevas{day} · {count} nuevas": check how `{day}` renders (English adds something like " · Monday").
