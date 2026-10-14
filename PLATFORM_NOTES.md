# PTA 平台逆向笔记（从 exam-app 生产前端 + 实测得到）

来源：`https://static.pintia.cn/exam-app/{main,*.chunk,vendor.*}.js`
（答题页是独立 app，与 `sparkling-daydream` 主站前端不是一套）

## 1. 提交接口

```js
createAPI({ name:"SubmitExamProblems", method:"POST", type:"MUTATE",
            path:"/api/exams/{exam_id}/exam-submissions",
            pathVariables:["examId"], body:["problemType","details"] })
```

`details[i] = { problemId:"0", problemSetProblemId:<exam-problems[].id>, <detailKey>:{...} }`

| problemType | detailKey | 内部字段 |
|---|---|---|
| TRUE_OR_FALSE | `trueOrFalseSubmissionDetail` | `{answer:"TRUE"\|"FALSE"}` |
| MULTIPLE_CHOICE | `multipleChoiceSubmissionDetail` | `{answer:"B"}` |
| MULTIPLE_CHOICE_MORE_THAN_ONE_ANSWER | `multipleChoiceMoreThanOneAnswerSubmissionDetail` | `{answers:["A","C"]}` |
| FILL_IN_THE_BLANK | `fillInTheBlankSubmissionDetail` | `{answers:[...]}` |
| FILL_IN_THE_BLANK_FOR_PROGRAMMING | `fillInTheBlankForProgrammingSubmissionDetail` | `{answers:[...]}` |
| CHOICE_BLANK | `choiceBlankSubmissionDetail` | `{answers:[...]}` |
| PROGRAMMING | `programmingSubmissionDetail` | `{program, compiler}` |
| CODE_COMPLETION | `codeCompletionSubmissionDetail` | `{program, compiler}` |
| SQL_PROGRAMMING | `sqlProgrammingSubmissionDetail` | `{program}` |

提交语义（实测）：
- **客观题**：前端 `onSave(){ (0,p.ad)({examId, problemType, details:(0,z.compact)(T)}, ...) }` —— 提交的是**该题型全部答案**，
  且服务端按题型整卷 upsert，后一次覆盖前一次。
- **编程/主观题**：`submitProblem(o){ (0,p.ad)({examId, problemType, details:[o]}) }` —— **逐题**提交，独立记录。
- 前端有 `autoSave:true`（答题过程中自动整卷保存），`[60,30,10,5]` 秒倒计时触发保存。

编译器枚举（`main.js`）：`GCC GXX CLANG CLANGXX JAVAC PYTHON2 PYTHON3 RUBY BASH CAT CLISP FPC GO GHC LUA ...`，
`compiler:"NO_COMPILER"` 表示未锁定，可选任意；默认示例用 `PYTHON3`。

## 2. 相关接口清单（实测可用）

```
GET  /api/problem-sets?filter=&limit=&order_by=END_AT&asc=true     # 含 examIdByProblemSetId
GET  /api/problem-sets/{psid}/exams                                # exam + problemSet(+config) + serverTime
GET  /api/problem-sets/{psid}/problem-summaries                    # 各题型题量与总分（单数 problem!）
GET  /api/problem-sets/{psid}/exam-problems?exam_id=&problem_type= # 题目列表（含 content/problemConfig）
GET  /api/problem-sets/{psid}/exam-problem-list?exam_id=&problem_type=&page=&limit=
GET  /api/exams/{exid}/problem-sets/{psid}/problem-status          # 学生视角每题状态
GET  /api/exams/{exid}/problem-sets/{psid}/last-submissions?problemType=[&problemSetProblemId=]
GET  /api/exams/{exid}/submissions/{submission_id}                 # 判题详情（含 testcaseJudgeResults）
GET  /api/problem-sets/{psid}/standard-answers?problem_type=       # 交卷前返回空
PUT  /api/exams/{exid}/end                                         # 交卷（触发客观题判题）
```

`exam-problems[]` 关键字段：`id` = **problemSetProblemId**（提交用这个；该数组中 `problemSetProblemId` 字段为 null，
`problemId` 是另一套题库 ID）；另有 `content`(Markdown) / `score` / `type` / `problemConfig` / `compiler`。

状态取值：`PROBLEM_NO_ANSWER` / `PROBLEM_SUBMITTED` / `PROBLEM_ACCEPTED` / `PROBLEM_WRONG_ANSWER` /
`PARTIAL_ACCEPTED`（提交级）；考试级 `PROCESSING` / `END`，另有 `ended`(学生是否已交卷)。

## 3. 请求特征（复刻要点）

- 登录：`POST passport.pintia.cn/api/users/sessions`，body 平铺 `{email|phone, password, rememberMe, ticket, randStr}`；
  前端是 **reactive** 流程——先发无票请求，网关回 `GATEWAY_WRONG_CAPTCHA` 才弹验证码，再带票重试。
- 前端初始化验证码：`new window.TencentCaptcha("194593025", cb)`（无第三参数/UId 绑定）。
- `X-Lollipop` / `X-Marshmallow`：SPA 在 `withCredentials` 的 XHR 上附带，前者是设备指纹库缓存的字符串；
  **实测这些只读接口不带也能 200**，非必需。
- 错误码语义：`GATEWAY_WRONG_CAPTCHA` 既可能是验证码错，也可能是**风控兜底**；
  `USER_NOT_FOUND`(404) 在未登录时出现，容易误判，工具里已归一成 `NOT_LOGGED_IN`。
- **路径写错的表现**：前端路由兜底返回 `index.html`，配合 `Accept: application/json` 会得到 **406 空响应体**，
  直接 `.json()` 抛 `JSONDecodeError`——工具里统一报 `NOT_JSON`。

## 4. 自动化注意事项

- 客观题必须**一次整卷提交**；编程题**逐题**、可用题目自带样例先本地自测（PTA 判题只有通过/不通过，无部分反馈）。
- 题目未写精度要求时按**原始浮点**输出（答案键即参考实现的输出）。
- 编程题判题即时（~1s），可用 `pta_submission_detail` 直接看失败用例编号与得分分布。
- 交卷前客观题无法自查（`standard-answers` 为空），如需满分应在交卷后核对再决定是否重做。
- 单 IP 高频解票/多次失败登录会进入风控窗口，登录与提交都应限频。
