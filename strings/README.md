# UI strings

`en.json` is the English source of truth: a flat key → plain-text value catalog. Copy it to `es.json` when ready; `oye-lang` in localStorage preserves the selection across page loads. Missing catalogs and missing keys fall back to English. The Home EN · ES toggle switches UI labels and dates immediately; Oye and lesson content stay unchanged.

Keep `{placeholders}` unchanged; values may be reordered. Do not add HTML. Callers escape at the HTML boundary. `_one` / `_many` keys preserve the existing singular/plural cases. Content JSON, hints, explanations, and Gabriel’s report copy are outside this catalog. Language-neutral digits, symbols, and structural punctuation stay with their markup.

`date.*_locale` contains Intl locale identifiers; `date.*_options` contains serialized Intl.DateTimeFormat options. Together they preserve the original home, batch, report, and mission dates. `date.range` and `date.with_year` control report range ordering.

The marked text and metadata in `index.html` are generated copies of these keys so loading and JavaScript-disabled behavior stay unchanged. Do not edit those copies: `tools/publish_content.py` refreshes them from English before generating the worker. To refresh only shell artifacts (without publishing content or building audio):

```sh
.venv/bin/python -c "import json; from tools.publish_content import update_sw; update_sw(json.load(open('content/version.json'))['version'])"
```

Length notes are recommended maxima in rendered characters (including spaces and substituted values), not truncation rules. They preserve the current tight English footprint; verify translations at 360px width. Labels wrap and containers grow; verify topic tiles, button rows, and report headings at 360px.

| Key | Where it appears | Max-length note |
| --- | --- | --- |
| `app.description` | `index.html` (generated boot text / metadata) | — |
| `app.load_error` | `js/app.js` (boot) | — |
| `app.loading` | `index.html` (generated boot text / metadata) | — |
| `app.name` | `index.html` (generated boot text / metadata); `js/app.js` (boot); `js/app.js` (goHome) | — |
| `app.needs_javascript` | `index.html` (generated boot text / metadata) | — |
| `app.no_content` | `js/content.js` (loadContent) | — |
| `audio.hear_again` | `js/app.js` (feedbackBody) | — |
| `audio.pause` | `js/app.js` (bindPlayer) | — |
| `audio.play` | `js/app.js` (bindPlayer); `js/app.js` (playerBar); `js/app.js` (reviewRow) | — |
| `audio.play_again` | `js/app.js` (LISTEN_CAP) | — |
| `audio.play_line` | `js/app.js` (dialogueHtml) | — |
| `audio.position` | `js/app.js` (playerBar) | — |
| `audio.replay` | `js/app.js` (missionIntro); `js/app.js` (replayBtn) | — |
| `audio.replay_slowly` | `js/app.js` (slowBtn) | — |
| `audio.slow` | `js/app.js` (audioHero) | — |
| `audio.slow_label` | `js/app.js` (playerBar) | — |
| `audio.slow_speed` | `js/app.js` (playerBar); `js/app.js` (slowBtn) | Max 5 characters; button row, one line. |
| `audio.stop` | `js/app.js` (replayBtn) | — |
| `audio.tap_to_listen` | `js/app.js` (LISTEN_CAP) | — |
| `audio.whole_clip` | `js/app.js` (openFeedbackSheet) | — |
| `card.after_answer` | `js/app.js` (renderSceneQuestion) | — |
| `card.alternate_answer` | `js/app.js` (answerLine) | — |
| `card.answer` | `js/app.js` (answerLine) | — |
| `card.check` | `js/app.js` (renderListenType); `js/app.js` (textInputRow) | Max 5 characters; button row, one line. |
| `card.correct` | `js/app.js` (resultRow) | — |
| `card.dont_know` | `js/app.js` (btnDontKnow) | Max 12 characters; button row, one line. |
| `card.drill_progress` | `js/app.js` (showCard) | — |
| `card.end_session` | `js/app.js` (showCard) | — |
| `card.feedback_dialog` | `js/app.js` (openFeedbackSheet) | — |
| `card.fix_it` | `js/app.js` (TYPE_LABEL); `js/app.js` (renderFixIt) | — |
| `card.from_class` | `js/app.js` (renderFixIt) | — |
| `card.from_mistakes` | `js/app.js` (renderFixIt) | — |
| `card.from_scene` | `js/app.js` (renderFixIt) | — |
| `card.hide_hint` | `js/app.js` (hintBox) | Max 4 characters; button row, one line. |
| `card.hint` | `js/app.js` (hintBox); `js/app.js` (hintPill) | Max 4 characters; button row, one line. |
| `card.listen` | `js/app.js` (TYPE_LABEL); `js/app.js` (renderListenPick) | — |
| `card.listen_prompt` | `js/app.js` (renderListenPick) | — |
| `card.listen_type` | `js/app.js` (TYPE_LABEL); `js/app.js` (renderListenType) | — |
| `card.next` | `js/app.js` (btnNext); `js/app.js` (missionQuestion) | Max 4 characters; button row, one line. |
| `card.not_quite` | `js/app.js` (resultRow) | — |
| `card.progress` | `js/app.js` (showCard) | — |
| `card.region` | `js/app.js` (openFeedbackSheet); `js/app.js` (openMissionRead); `js/app.js` (openMissionTranscript) | — |
| `card.scene` | `js/app.js` (TYPE_LABEL); `js/app.js` (renderSceneQuestion) | — |
| `card.transcript` | `js/app.js` (openMissionTranscript); `js/app.js` (renderSceneQuestion) | — |
| `card.type_prompt` | `js/app.js` (renderListenType) | — |
| `card.what_you_heard` | `js/app.js` (feedbackRows); `js/app.js` (openFeedbackSheet) | — |
| `card.whole_scene` | `js/app.js` (openFeedbackSheet) | — |
| `card.why` | `js/app.js` (feedbackRows); `js/app.js` (openFeedbackSheet) | — |
| `card.you_typed` | `js/app.js` (answerLine) | — |
| `card.you_wrote` | `js/app.js` (answerLine) | — |
| `card.your_answer` | `js/app.js` (missionQuestion); `js/app.js` (textInputRow) | — |
| `card.your_reply` | `js/app.js` (TYPE_LABEL); `js/app.js` (renderReply) | — |
| `common.back` | `js/app.js` (missionQuestion); `js/app.js` (openMissionRead) | Max 4 characters; button row, one line. |
| `common.close` | `js/app.js` (openFeedbackSheet); `js/app.js` (openSimpleSheet) | Max 5 characters; button row, one line. |
| `common.done` | `js/app.js` (missionResult); `js/app.js` (showDrillSummary); `js/app.js` (showSummary); `js/app.js` (showTopicSummary) | Max 4 characters; button row, one line. |
| `common.home` | `js/app.js` (openMissions); `js/app.js` (openReports); `js/app.js` (openTopics) | — |
| `common.more_below` | `js/app.js` (moreHint) | Max 10 characters; button row, one line. |
| `date.batch_locale` | `js/app.js` (batchLine) | — |
| `date.batch_options` | `js/app.js` (batchLine) | — |
| `date.home_locale` | `js/app.js` (fmtDate) | — |
| `date.home_options` | `js/app.js` (fmtDate) | — |
| `date.range` | `js/app.js` (weekRange) | — |
| `date.short_locale` | `js/app.js` (missionRows); `js/app.js` (monthDay) | — |
| `date.short_options` | `js/app.js` (missionRows); `js/app.js` (monthDay) | — |
| `date.with_year` | `js/app.js` (weekRange) | — |
| `drill.another` | `js/app.js` (showDrillSummary) | Max 10 characters; button row, one line. |
| `drill.missed` | `js/app.js` (showDrillSummary) | — |
| `drill.nothing_missed` | `js/app.js` (showDrillSummary) | — |
| `drill.practice` | `js/app.js` (showDrillSummary) | — |
| `duration.about_minutes` | `js/app.js` (clipMinutes) | — |
| `duration.minutes_seconds` | `js/app.js` (fmtDuration) | — |
| `duration.seconds` | `js/app.js` (fmtDuration) | — |
| `folder.cafes_food` | `js/app.js` (FOLDERS) | Max 20 characters; mission topic row. |
| `folder.city_life` | `js/app.js` (FOLDERS) | Max 20 characters; mission topic row. |
| `folder.design_art` | `js/app.js` (FOLDERS) | Max 20 characters; mission topic row. |
| `folder.fashion` | `js/app.js` (FOLDERS) | Max 20 characters; mission topic row. |
| `folder.fitness_health` | `js/app.js` (FOLDERS) | Max 20 characters; mission topic row. |
| `folder.home_errands` | `js/app.js` (FOLDERS) | Max 20 characters; mission topic row. |
| `folder.lgbtq_culture` | `js/app.js` (FOLDERS) | Max 20 characters; mission topic row. |
| `folder.music` | `js/app.js` (FOLDERS) | Max 20 characters; mission topic row. |
| `folder.scifi_tech` | `js/app.js` (FOLDERS) | Max 20 characters; mission topic row. |
| `folder.travel` | `js/app.js` (FOLDERS) | Max 20 characters; mission topic row. |
| `format.email` | `js/app.js` (FORMAT_LABEL) | — |
| `format.label` | `js/app.js` (FORMAT_LABEL) | — |
| `format.menu` | `js/app.js` (FORMAT_LABEL) | — |
| `format.receipt` | `js/app.js` (FORMAT_LABEL) | — |
| `format.sign` | `js/app.js` (FORMAT_LABEL) | — |
| `format.whatsapp` | `js/app.js` (FORMAT_LABEL) | — |
| `home.challenges` | `js/app.js` (goHome) | — |
| `home.choose_lesson` | `js/app.js` (goHome) | — |
| `home.drill_many` | `js/app.js` (drillRow) | — |
| `home.drill_one` | `js/app.js` (drillRow) | — |
| `home.mission` | `js/app.js` (goHome) | — |
| `home.mission_description` | `js/app.js` (goHome) | — |
| `home.mix` | `js/app.js` (goHome) | Max 3 characters; button row, one line. |
| `home.new_batch` | `js/app.js` (batchLine) | — |
| `home.no_cards` | `js/app.js` (goHome) | — |
| `home.quick_session` | `js/app.js` (goHome) | — |
| `home.saving_audio` | `js/app.js` (quietLine) | — |
| `home.session_description` | `js/app.js` (goHome) | — |
| `home.topic_challenge` | `js/app.js` (goHome); `js/app.js` (openTopics) | — |
| `home.topic_description` | `js/app.js` (goHome) | — |
| `home.verb_drill` | `js/app.js` (drillRow); `js/app.js` (showDrillSummary) | — |
| `home.your_week` | `js/app.js` (goHome); `js/app.js` (openReports) | — |
| `keypad.delete` | `js/app.js` (keypadHtml); `js/app.js` (renderListenType) | — |
| `lessons.all` | `js/app.js` (openLessons) | — |
| `lessons.card_count` | `js/app.js` (openLessons) | — |
| `lessons.name` | `js/app.js` (lessonName) | — |
| `lessons.title` | `js/app.js` (openLessons) | — |
| `level.easy` | `js/app.js` (goHome) | — |
| `level.hard` | `js/app.js` (goHome); `js/app.js` (openMissions) | — |
| `level.medium` | `js/app.js` (goHome); `js/app.js` (openTopics); `js/app.js` (showTopicSummary) | — |
| `mission.alternate_answer` | `js/app.js` (missionResult) | — |
| `mission.audio` | `js/app.js` (missionIntro) | — |
| `mission.audio_duration` | `js/app.js` (missionRows) | — |
| `mission.back_intro` | `js/app.js` (READ_BACK) | — |
| `mission.back_question` | `js/app.js` (missionMessage) | Max 19 characters; button row, one line. |
| `mission.back_results` | `js/app.js` (READ_BACK) | — |
| `mission.by_topic` | `js/app.js` (openMissions) | — |
| `mission.correct_answer` | `js/app.js` (missionResult) | — |
| `mission.count_many` | `js/app.js` (openMissions) | — |
| `mission.count_one` | `js/app.js` (openMissions) | — |
| `mission.done` | `js/app.js` (openMissions) | — |
| `mission.done_count` | `js/app.js` (openMissions) | — |
| `mission.end` | `js/app.js` (missionTop) | — |
| `mission.folder_note` | `js/app.js` (openMissions) | — |
| `mission.folder_status` | `js/app.js` (openMissions) | — |
| `mission.go_questions` | `js/app.js` (missionMessage) | Max 15 characters; button row, one line. |
| `mission.hard_message` | `js/app.js` (openMissionRead) | — |
| `mission.hard_mission` | `js/app.js` (missionIntro) | — |
| `mission.hard_script` | `js/app.js` (openMissionRead) | — |
| `mission.hard_topic` | `js/app.js` (openMissions) | — |
| `mission.in_english` | `js/app.js` (openMissionRead); `js/app.js` (openMissionTranscript) | — |
| `mission.message` | `js/app.js` (missionIntro); `js/app.js` (missionRows) | — |
| `mission.message_from` | `js/app.js` (msgRowLabel) | — |
| `mission.missions` | `js/app.js` (READ_BACK); `js/app.js` (openMissions) | — |
| `mission.new_best` | `js/app.js` (missionResult) | — |
| `mission.new_recent` | `js/app.js` (openMissions) | — |
| `mission.newest_first` | `js/app.js` (openMissions) | — |
| `mission.not_tried` | `js/app.js` (openMissions) | — |
| `mission.open_label` | `js/app.js` (missionRows) | — |
| `mission.preview` | `js/app.js` (missionIntro) | Max 24 characters; button row, one line. |
| `mission.progress` | `js/app.js` (missionQuestion) | — |
| `mission.question_count` | `js/app.js` (missionIntro) | — |
| `mission.questions` | `js/app.js` (missionIntro) | — |
| `mission.read` | `js/app.js` (missionRows) | Max 4 characters; button row, one line. |
| `mission.read_label` | `js/app.js` (missionRows) | — |
| `mission.read_message` | `js/app.js` (missionIntro) | Max 16 characters; button row, one line. |
| `mission.read_message_again` | `js/app.js` (msgRowLabel) | — |
| `mission.read_script` | `js/app.js` (missionIntro) | Max 11 characters; button row, one line. |
| `mission.read_the_script` | `js/app.js` (missionResult) | Max 15 characters; button row, one line. |
| `mission.read_then_answer` | `js/app.js` (missionMessage) | — |
| `mission.replay_note` | `js/app.js` (missionIntro) | — |
| `mission.reread` | `js/app.js` (missionIntro) | — |
| `mission.reread_note` | `js/app.js` (missionIntro) | — |
| `mission.see_results` | `js/app.js` (missionQuestion) | Max 11 characters; button row, one line. |
| `mission.stars` | `js/app.js` (missionResult) | — |
| `mission.stars_with_hints` | `js/app.js` (missionResult) | — |
| `mission.start` | `js/app.js` (missionIntro) | Max 13 characters; button row, one line. |
| `mission.text` | `js/app.js` (missionIntro) | — |
| `mission.the_message` | `js/app.js` (openMissionTranscript) | — |
| `mission.title` | `js/app.js` (openMissions) | — |
| `mission.transcript_note` | `js/app.js` (missionIntro) | — |
| `mission.try_again` | `js/app.js` (missionResult) | Max 9 characters; button row, one line. |
| `mission.you_picked` | `js/app.js` (missionResult) | — |
| `mission.you_typed` | `js/app.js` (missionResult) | — |
| `reports.added` | `js/app.js` (openReport) | — |
| `reports.author` | `js/app.js` (openReport); `js/app.js` (openReports); `js/app.js` (resultsStatusHtml) | — |
| `reports.back` | `js/app.js` (openReport) | — |
| `reports.batch_note` | `js/app.js` (openReports) | — |
| `reports.by_challenge` | `js/app.js` (openReport) | — |
| `reports.byline` | `js/app.js` (openReports) | — |
| `reports.connect` | `js/app.js` (openReport) | — |
| `reports.empty` | `js/app.js` (openReports) | — |
| `reports.empty_detail` | `js/app.js` (openReports) | — |
| `reports.first_arrives` | `js/app.js` (reportCaption) | — |
| `reports.focus` | `js/app.js` (openReport) | Max 15 characters; heading, one line. |
| `reports.loading` | `js/app.js` (openReport) | — |
| `reports.mistake_count` | `js/app.js` (openReport) | — |
| `reports.mistakes` | `js/app.js` (openReport) | — |
| `reports.new` | `js/app.js` (newBadge) | — |
| `reports.no_tests` | `js/app.js` (openReports) | — |
| `reports.percent_correct` | `js/app.js` (openReport) | — |
| `reports.tests` | `js/app.js` (openReport) | — |
| `reports.title` | `js/app.js` (openReports) | — |
| `reports.total` | `js/app.js` (openReports) | — |
| `reports.unavailable` | `js/app.js` (openReport) | — |
| `reports.week_of` | `js/app.js` (reportCaption) | — |
| `reports.weekly_report` | `js/app.js` (goHome); `js/app.js` (openReport) | — |
| `reports.wrong` | `js/app.js` (openReport) | — |
| `results.queued` | `js/app.js` (resultsStatusHtml) | — |
| `results.sent` | `js/app.js` (resultsStatusHtml) | — |
| `review.skipped` | `js/app.js` (reviewRow); `js/app.js` (showDrillSummary) | — |
| `review.you_picked` | `js/app.js` (reviewRow) | — |
| `review.you_typed` | `js/app.js` (reviewRow) | — |
| `review.you_wrote` | `js/app.js` (reviewRow) | — |
| `score.best` | `js/app.js` (bestScore) | — |
| `score.done` | `js/app.js` (bestScore) | — |
| `score.not_tried` | `js/app.js` (bestScore); `js/app.js` (scoreRow) | — |
| `stars.hint_half` | `js/app.js` (starsSentence) | — |
| `stars.label` | `js/app.js` (starRow) | — |
| `stars.new_best` | `js/app.js` (showTopicSummary) | — |
| `stars.one` | `js/app.js` (starsSentence) | — |
| `stars.previous` | `js/app.js` (showTopicSummary) | — |
| `stars.three` | `js/app.js` (starsSentence) | — |
| `stars.topic_total` | `js/app.js` (showTopicSummary) | — |
| `stars.two` | `js/app.js` (starsSentence) | — |
| `stars.two_with_hints` | `js/app.js` (starsSentence) | — |
| `summary.another_topic` | `js/app.js` (showTopicSummary) | Max 13 characters; button row, one line. |
| `summary.back_tomorrow` | `js/app.js` (showSummary) | — |
| `summary.breakdown_score` | `js/app.js` (showTopicSummary) | — |
| `summary.cards_today` | `js/app.js` (showSummary) | — |
| `summary.choosing` | `js/app.js` (showTopicSummary) | — |
| `summary.correct` | `js/app.js` (openReport); `js/app.js` (showSummary) | — |
| `summary.day_streak` | `js/app.js` (showSummary) | — |
| `summary.hints_used` | `js/app.js` (showTopicSummary) | — |
| `summary.listening` | `js/app.js` (showTopicSummary) | — |
| `summary.minutes` | `js/app.js` (showSummary) | — |
| `summary.nothing_to_review` | `js/app.js` (showSummary) | — |
| `summary.review_again` | `js/app.js` (showSummary) | — |
| `summary.score` | `js/app.js` (missionResult); `js/app.js` (showDrillSummary); `js/app.js` (showTopicSummary) | — |
| `summary.session_done` | `js/app.js` (showSummary) | — |
| `summary.time` | `js/app.js` (showTopicSummary) | — |
| `summary.typing` | `js/app.js` (showTopicSummary) | — |
| `tag.pronoun_pairs` | `js/app.js` (TAG_LABEL) | — |
| `tag.ser_estar` | `js/app.js` (TAG_LABEL) | — |
| `tag.tu_vs_usted` | `js/app.js` (TAG_LABEL) | — |
| `tag.versus` | `js/app.js` (tagLabel) | — |
| `topic.directions` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.food_ordering` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.health_pharmacy` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.home_errands` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.numbers_prices` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.phone_reservations` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.shopping` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.small_talk` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.time_schedules` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.verbs_commands` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.verbs_future` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.verbs_past` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.verbs_present` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topic.weather_plans` | `js/levels.js` (TOPICS) | Max 20 characters; topic tile, at most two lines. |
| `topics.coming_soon` | `js/app.js` (openTopics) | — |
| `topics.instructions` | `js/app.js` (openTopics) | — |
