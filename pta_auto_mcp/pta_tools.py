import logging
from datetime import datetime, timezone
from typing import Any

import requests

from . import login as P

log = logging.getLogger("pta-tools")

DETAIL_FIELD = {
    "TRUE_OR_FALSE": "trueOrFalseSubmissionDetail",
    "MULTIPLE_CHOICE": "multipleChoiceSubmissionDetail",
    "MULTIPLE_CHOICE_MORE_THAN_ONE_ANSWER": "multipleChoiceMoreThanOneAnswerSubmissionDetail",
    "FILL_IN_THE_BLANK": "fillInTheBlankSubmissionDetail",
    "FILL_IN_THE_BLANK_FOR_PROGRAMMING": "fillInTheBlankForProgrammingSubmissionDetail",
    "CHOICE_BLANK": "choiceBlankSubmissionDetail",
    "PROGRAMMING": "programmingSubmissionDetail",
    "CODE_COMPLETION": "codeCompletionSubmissionDetail",
    "SQL_PROGRAMMING": "sqlProgrammingSubmissionDetail",
}

class PTAError(RuntimeError):
    def __init__(self, code: str, message: str = "", http: int | None = None):
        super().__init__(message or code)
        self.code, self.message, self.http = code, message, http


class PTASession:
    def __init__(self,session:requests.Session):
        self.session = session

    def request(self,method: str, path: str, params: dict | None = None, body: dict | None = None,
                 timeout: int = 25, base: str = P.BASE) -> Any:
        r = self.session.request(method, base + path, params=params, json=body, timeout=timeout)
        ct = r.headers.get("content-type", "")
        if r.status_code != 200:
            code = f"HTTP_{r.status_code}"
            try:
                code = r.json().get("error", {}).get("code", code)
            except ValueError:  # 错误响应不是 JSON 时沿用 HTTP_x
                log.warning("非 JSON 错误响应: %s", r.text[:120])
            raise PTAError(code, f"HTTP {r.status_code} ct={ct} body={r.text[:300]}", r.status_code)
        if "json" not in ct:
            raise PTAError("NOT_JSON", f"非 JSON 响应（多半路径不存在被 SPA 兜底）ct={ct} body={r.text[:120]}",
                           r.status_code)
        return r.json()
    def get(self,path: str,**kw) -> Any:
        return self.request("GET", path, **kw)

    def post(self,path: str, body: dict, **kw) -> Any:
        return self.request("POST", path, body=body, **kw)

session = PTASession(P.new_session(P.COOKIE_FILE))


def get_exam_id(limit: int=20, only_alive: bool=True):
    '''get the exams' problem_set_id ,exam_id ,name ,duration.U should decide which exam to do'''
    time_point = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.000Z')
    template = '{"endAtAfter":"' + time_point + '"}'  # 只要未结束的练习/考试
    # filter 交给 requests 编码，不能自己 quote()（二次编码会被服务端拒绝）
    params = {"filter": template if only_alive else "", "limit": limit,
              "order_by": "END_AT", "asc": "true"}
    data = session.get("/api/problem-sets", params=params)
    exam_ids = data.get("examIdByProblemSetId") or {}
    return [{"problem_set_id": p["id"], "exam_id": exam_ids.get(p["id"]), "name": p.get("name"),
             "startAt": p.get("startAt"), "endAt": p.get("endAt")}
            for p in data.get("problemSets", [])]

def summaries_check(exam_id:str,type_sumup:dict):
    '''request the summaries and save the summaries for total check after pull the full exam.The sum up should be like:{"TRUE_OR_FALSE":38,"MULTIPLE_CHOICE":68,"PROGRAMMING": 18}'''
    summaries=session.get(f"/api/problem-sets/{exam_id}/problem-summaries")
    problem_types = list(summaries['summaries'].keys())
    group={}
    for ptype in problem_types:
        group[ptype]=summaries['summaries'][ptype]['total']
    return group == type_sumup

def pull_exam_set(exam_id:str):
    '''request the API,loop and save all problem via different types (leaked from summaries)'''
    summaries = session.get(f"/api/problem-sets/{exam_id}/problem-summaries")
    problem_types = list(summaries['summaries'].keys())
    problem_set={}
    for ptype in problem_types:
        content = session.get(f"/api/problem-sets/{exam_id}/exam-problems", params={"problem_type": ptype})
        for p in content.get("problemSetProblems", []):
            pid = p["id"]
            problem_set.setdefault(ptype, []).append({
                "id": pid,
                "content": p.get("content", ""),
            })
    sum_up={k: len(v) for k, v in problem_set.items()}
    if summaries_check(exam_id,sum_up):
        #log success here
        return problem_set
    else:
        raise PTAError("INCORRECT_NUMBER","拉取到的题目数量和平台上统计的题目数量不一致!")


def submit_nonwritable(exam_id:str,batched_answer:dict,answer_type:str):
    '''submit objective answers: 单答案用 answer, 多答案/填空题用 answers.
    your answer should be like:{"problemType":"MULTIPLE_CHOICE","details":[{"problemSetProblemId":"2099334496411578418","answer":"C"},{"problemSetProblemId":"2099334496411578419","answer":"B"}]}'''
    submit_Template={"problemType":answer_type,"details":[]}
    submit_Template["details"] = [
        {
            "problemId": "0",
            "problemSetProblemId": item["problemSetProblemId"],
            DETAIL_FIELD[answer_type]: ({"answers": item["answers"]} if "answers" in item
                                        else {"answer": item["answer"]}),
        }
        for item in batched_answer["details"]
    ]
    return session.post(f"/api/exams/{exam_id}/exam-submissions", submit_Template, timeout=60)


def submit_writable(exam_id:str,batched_answer:dict,answer_type:str,program_type:str):
    '''submit programing problem and get status,remember to attach the type of program.your answer should be like:{"problemType":"PROGRAMMING","details":[{"problemSetProblemId":"2104474233634361469","program":"eval(1+1)"}]}'''
    submit_Template = {"problemType": answer_type, "details": []}
    item=batched_answer["details"][0]
    submit_Template["details"] = [
        {
            "problemId": "0",
            "problemSetProblemId": item["problemSetProblemId"],
            "programmingSubmissionDetail": {
                "program": item["program"],
                "compiler": program_type,
            },
        }
    ]
    resp = session.post(f"/api/exams/{exam_id}/exam-submissions", submit_Template, timeout=60)
    return check_program_status(exam_id, resp["submissionId"])


def check_program_status(exam_id:str,submission_id:str,max_text:int = 200):
    '''check the status of programing which will be judged in turn'''
    status=session.get(f"/api/exams/{exam_id}/submissions/{submission_id}").get("submission")
    cases, failing = [], []
    for c in status.get("judgeResponseContents") or []:
        pr = c.get("programmingJudgeResponseContent") or {}
        for cid, t in (pr.get("testcaseJudgeResults") or {}).items():
            res = t.get("result")
            cases.append({"case": cid, "result": res, "score": t.get("testcaseScore"),
                          "time_ms": round((t.get("time") or 0) * 1000)})
            if res != "ACCEPTED":
                failing.append({"case": cid, "result": res,
                                "stdout": str(t.get("stdout") or "")[:max_text],
                                "stderr": str(t.get("stderr") or "")[:max_text],
                                "error": str(t.get("error") or "")[:max_text]})
    return {"submission_id": submission_id, "status": status.get("status"), "score": status.get("score"),
            "submitAt": status.get("submitAt"),
            "problem_set_problem_id": (status.get("submissionDetails") or [{}])[0].get("problemSetProblemId"),
            "cases": cases or None, "failing": failing or None}


