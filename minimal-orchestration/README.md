# Subject

MOTOMIND·ROBROS·Figure AI의 제조·물류용 휴머노이드 사업화 가능성을 **Orchestrator-Workers**로 조사하고, **세 기업을 종합한 하나의 최종 투자 보고서**를 코드로 생성한다. 기존 RAG의 PDF 검색·원문 검증·21항목 평가표를 재사용한다.

## Overview

- **Objective:** 세 기업의 기술·고객·사업화 근거를 공통 기준으로 비교하고, 최종 선택 또는 보류의 이유와 투자 전 조건을 설명한다.
- **Pattern:** Orchestrator-Workers — 부족한 질문을 계획하고 필요한 전문 워커에 병렬 배분하기에 적합하다.
- **동적 처리:** 최초에도 필요한 0~3개 워커만 선택한다. Synthesizer가 누적 결과를 확인해 미달 항목만 재조사하며, 동일 작업 반복과 무진전 조사를 중단한다.
- **출력:** 기본 실행은 기업별 전체 보고서와 검증 State를 종합해 `final_investment_report.md/.pdf/.json`을 생성한다. **최종 제출 PDF 한 파일 전체가 최대 10쪽**이며, 기업별 한 쪽 요약·점수·trace는 중간 산출물이다.

선정 기준은 **최종 보고서의 근거 연결·평가 누락 통제와 피드백 비용**이다. Supervisor도 병렬 워커와 품질 평가 루프를 구현할 수 있다. 본 과제는 18개 자동 조사 항목별 질문·역할을 구조화 계획으로 먼저 기록하고, `Send` 결과가 합류한 뒤 근거를 검증하는 Orchestrator-Workers가 적합하다고 판단했다. 표현 결함은 같은 근거로 수정하고 부족한 항목만 재조사하는 루프로 보고서 품질을 유지하면서 불필요한 워커 호출을 줄이도록 설계했다.

## Selected Technologies

- **SW:** 로봇 인지·제어 AI와 VLA 행동 모델 — 작업 성능, 일반화, 학습·데이터 자산을 검토한다.
- **HW:** 휴머노이드 플랫폼 — 제조·물류의 안정성, 양산·정비, 설비 연동을 검토한다.

기술명과 성능은 해당 기업의 검증된 원문을 보고서에 연결하며, 회사 발표와 독립 검증을 구분한다.

## Features

- **PDF RAG:** 등록된 [원천 PDF](rag/data/catalog.csv) 풀은 22개·106쪽이다. 기업 필터와 원본 페이지를 보존해 검색하며, `com_data/`는 조사 단서로 사용하고 등록 원문 URL만 추가로 읽는다.
- **고정 평가표:** [설계 PDF](evaluation/physical_ai_investment_design.pdf) 2~3쪽의 6영역·21항목·100점을 코드와 대조한다. 등급은 0~4 또는 U, 점수는 `배점 × 등급 ÷ 4`다. U를 0점으로 채우거나 100점으로 재환산하지 않는다.
- **확증 편향 통제:** 출처 없는 주장·부분 입증을 제외하고 반증·U를 보존한다. 구매 계약을 매출로, 첫 협력을 재구매로 채점하지 않도록 거래 방향·단계·같은 고객을 검증한다.
- **기업 보고서 합성:** 전체 본문·SHA256·문단 ID와 검증 State·원문을 `CompanyReportSynthesis`에 입력한다. 회사별 5필드와 비교 2문단을 작성하며, 분석을 가져온 문단과 실제 원문 인용이 등장한 문단을 `report_paragraph_ids`·`evidence_report_paragraph_ids`로 구분해 반영 맵에 보존한다.
- **품질 평가:** 형식·점수·인용 검사와 원본 페이지를 읽는 LLM Judge를 결합한 **Hybrid** 방식. Groundedness·중립성·편향 통제 세 축만 평가하며 관점 커버리지는 제외한다. 표현 결함은 재작성, 근거·채점 결함은 재조사한다. 실행에서 품질 오분류와 작성 형식 실패를 확인해 작성·선정·검증 모델을 별도 설정했으며, 개선 효과는 미측정이다.
- **최종 대상 선정:** `PortfolioDecision`이 같은 합성 분석과 원래 기업 보고서를 읽어 대상·비교 이유·필수 조건을 선정한다. 코드가 근거 연결·핵심 부정 사실·거래조건을 검사하며, 최고 총점 기업을 자동 선택하지 않는다.
- **통합 보고서:** 점수표·U·REFERENCE는 코드가 고정하고 기업별 해석·한계·현재 의견을 합성 본문으로 반영한다. 길이·문장 완결성 검사와 `report_language.py`가 명백한 절단·자료 부재 단정·독립 입증 과장을 거절한다. 실제 문단의 인용↔원문 페이지를 대조하는 검사로 RL·MOU 목적·IFR 수요·공공 실증의 알려진 범위 오류를 차단하며, 원문·검증 State를 우선해 교정한다.

Judge의 `findings`는 `{criterion, basis, paragraph_id, defect, action}`으로 실제 보고서 문단에 결함을 연결한다. 같은 축의 결함이 있으면 코드가 그 축을 실패로 판정하고, 통과 여부와 `issues`를 이 목록에서 계산해 중복 판정의 모순을 방지한다. 단순 자료 부재·U는 `data_gaps`이며 품질 실패 사유가 아니다.

통합 Judge는 같은 호출에서 핵심 3축과 별도로 세 기업의 분석·한계·개별 의견 반영 여부를 검사한다. 이는 관점 커버리지가 아니라 입력 보고서의 실제 종합 여부를 확인하는 검사다.

거래조건 미확인은 조건부 투자로 제한하고, 핵심 근거가 부족하면 선정을 보류한다. 선정 시 최종 보고서에 “필수조건을 충족하면 선정 기업에 투자한다”는 결론과 조건을 연결한다. 보고서는 코드가 생성하는 투자 심사 자료이며 실제 거래 실행을 자동 결정하지 않는다.

## Tech Stack

| 구분 | 구현 |
|---|---|
| Framework | LangGraph `StateGraph`, `Send`, 조건 분기 |
| LLM / 계획·워커 | 현재 설정: `gpt-4.1-mini`(`OPENAI_MODEL`) |
| LLM / 기업별 작성·검증 | `gpt-4.1`: `OPENAI_WRITER_MODEL`·`OPENAI_EVIDENCE_MODEL`·`OPENAI_GRADER_MODEL`·`OPENAI_JUDGE_MODEL` |
| LLM / 최종 합성·선정·반영 Judge | `gpt-5.4`: `OPENAI_SYNTHESIS_MODEL`·`OPENAI_PORTFOLIO_MODEL`·`OPENAI_COMBINED_JUDGE_MODEL`; 현재 `.env.example` 설정이며 모델 변경의 품질 개선 효과는 미측정 |
| Retrieval | NumPy dense 검색, 기업·COMMON 필터, 디스크 인덱스; Hit Rate@K·MRR **미측정** |
| Embedding | 오픈소스 `BAAI/bge-m3` / sentence-transformers |
| PDF | pypdf·pdfplumber·ReportLab, 한글 폰트 임베딩 |
| Observability | LangSmith + 로컬 trace JSON, 노드 시간·관측 사용량 |
| Checkpoint | `InMemorySaver`; 프로세스 재시작 후 일반 조사 재개는 미지원 |

역할별 설정은 변경 가능하다. 합성·통합 수정은 `OPENAI_SYNTHESIS_MODEL`→`OPENAI_WRITER_MODEL`→`OPENAI_MODEL`, 통합 Judge는 `OPENAI_COMBINED_JUDGE_MODEL`→`OPENAI_JUDGE_MODEL`→`OPENAI_MODEL` 순으로 대체하며 다른 역할도 미지정 시 기본 모델을 사용한다.

## Agents

| 구성 | 책임 |
|---|---|
| Orchestrator | 카탈로그·항목별 부족 사유로 `{id, worker, query, criteria, urls}` 계획 작성 |
| Bigtech Worker | 지분 투자, 고객 계약·납품, 유상 매출·재구매 조사 |
| Technology Worker | 팀, 작업 성능·안전·양산, 모방하기 어려운 자산 조사 |
| Market Worker | 고객·시장·경쟁 가치·전환 장벽 조사 |
| Synthesizer | 병렬 결과 병합, 원문 검증, 미달·무진전 판정 |
| Investment Judge / Case | 21항목 등급 검증·점수 계산, 근거에 연결된 최종 투자 제안 |
| Writer / Evaluator | 보고서 작성 후 세 품질 기준 검사; 재작성·재조사·종료 분기 |
| CompanyReportSynthesis | 실제 기업 보고서 전체·문단·원문을 읽어 5필드 분석·비교 2문단 합성 |
| PortfolioDecision | 3기업 검증 결과로 최종 대상·투자/조건부 투자/보류 선정; 총점 자동선택 금지 |
| Combined Report / Quality | 고정 점수표·합성 본문·선정 결론 통합, 원문 품질·기업 분석 반영 검사 |

워커는 결과만 반환하며 공용 계획·예산을 변경하거나 서로 직접 통신하지 않는다.

## State Schema

| 설계 항목 | 반영 방식 |
|---|---|
| 제어 vs 페이로드 | 계획·route·feedback·예산과 research·investment·report 분리 |
| 관측성 위치 | State에 현재 부족 사유·시도·종료 사유, 외부 JSON/LangSmith에 노드 기록 |
| 지속성 비용 | PDF·벡터는 파일/캐시, 유한 라운드 결과만 누적; 전체 State 바이트 상한은 없음 |
| 상관 | `trace_id` ↔ checkpoint `thread_id`; 실제 graph run UUID도 기록 |
| 재개/복구 | 조사 종료 후 Writer→Evaluator 복구; 품질 통과 후 PDF만 실패하면 모델 없이 재렌더링 |
| 동시 처리 | `라운드:작업ID` reducer 병합·충돌 검출; 카탈로그 병합은 Synthesizer만 수행 |
| 종료 보장 | 추가 조사 3~5회(기본 4), 무진전 2회 중단, 보고서 재작성 최대 2회, recursion 100 |

`completed`는 조사·품질 통과, `partial`은 U/조사 한계가 남은 품질 통과 보고서, `quality_failed`는 품질 미달 초안이다. U와 보고서 품질 실패는 별개다. 전체 실행 시간·비용 상한과 DB 기반 지속 복구는 추가 구현 대상이다.

## Architecture

![기업별 조사와 최종 투자 대상 선정·통합 보고서 구조](docs/architecture.png)

[SVG 원본](docs/architecture.svg) · 재생성: `.venv/bin/python scripts/render_architecture.py --require-integrated-batch`

상단은 실제 기업별 StateGraph, 하단은 `app.py`의 3기업 배치·최종 대상 선정·통합 보고서 처리다. 하단 함수를 추가 LangGraph 노드로 표시하지 않으며, 이 구조 이미지는 실제 LangSmith 실행 화면과 별개다.

표현·중립성 문제는 근거와 점수를 유지하는 재작성 루프에서 처리하고, 새 근거가 필요한 문제만 조사 루프로 돌린다. 성공 결과와 같은 원문의 검증을 재사용하고, 완전 반복 작업·무진전 재조사를 중단해 피드백 과정의 불필요한 호출을 줄이도록 설계했다. 실제 Supervisor 구현과의 비교 실험 및 비용 절감률은 측정하지 않았다.

통합 품질 평가도 `rewrite`이면 원문·점수·인용·선정 조건을 보존하는 `quality_reviser`로 최대 두 번 재작성·재평가하고, `research` 또는 예산 소진이면 미달 초안을 보존한다.

새 통합 경로는 검색·임베딩·재채점 없이 합성 LLM 기본 1회를 추가하며, 연결 형식 오류 시 1회만 교정한다. 같은 품질 Judge 호출에 반영 검사를 포함한다. 합성·품질 미달·재작성과 동일 입력 분석 재사용을 실제 실행에서 확인했다. 비용·품질 개선률은 미측정이다.

## Directory Structure

```text
app.py                    실행·배치·복구·LangSmith 설정·산출물 저장
agent_pipeline.py         계획·배분·합류·품질 루프
state_schema.py           공유 State·병렬 reducer
legacy_adapter.py         기존 RAG 연결·계획/보고서 Judge
criteria_registry.py      평가항목·담당 워커 매핑
criteria_coverage.py       항목별 조사 상태·결과 병합
investment_evaluation.py  등급·거래 단계 보호·점수 계산
investment_case.py        최종 투자 제안·지지 사실·필수 조건
portfolio_decision.py     검증 근거 기반 최종 투자 대상 선정·코드 보호 조건
company_report_inputs.py  기업 보고서 전체·SHA256·문단 ID·원문 인용 입력
report_synthesis.py       원보고서 5필드 분석·비교 합성·문단/근거 반영 맵
report_language.py        명백한 부재 단정·범위 과장 표현 검사
combined_report.py        3기업 종합·통합 품질 검사·최종 제출 보고서
combined_quality.py       통합 보고서의 원문 기반 세 품질 기준 Judge
company_brief.py          검증된 기업 결과를 각 A4 한 쪽 중간 요약으로 출력
saved_revalidation.py     저장된 사실 원문 재검증·재채점·보고서 교정 (새 검색 없음)
scripts/render_architecture.py  소스 구조 확인·SVG/PNG 아키텍처 생성
docs/architecture.*       제출용 구조 이미지 (실행 trace와 별개)
report_renderer.py        A4 양식·점수 강조·규격 검사
llm_client.py             요청 제한·LangSmith 모델 호출 추적
evaluation/               평가 기준 PDF (RAG 입력에서 제외)
rag/data/                 원천 PDF·catalog.csv; 원본 data 폴더 우선
com_data/                 기업별 조사 단서 PDF; 원본 폴더 우선
output/                   기업 보고서·점수·trace·비교표·웹 원문 캐시
tests/                    오프라인 회귀·원문/점수·그래프·PDF 검사
```

## Usage

```bash
cd minimal-orchestration
python3 -m venv .venv                        # 가상환경이 없을 때
.venv/bin/python -m pip install -r requirements-rag.txt
cp .env.example .env                         # .env가 없을 때만
# .env에 OPENAI_API_KEY, LANGSMITH_API_KEY 입력
.venv/bin/python app.py                      # 기본: 3기업 조사 → 최종 선정 → 통합 보고서
```

`--company`를 생략하면 세 기업 전체를 실행한다. 한 기업은 `--company MOTOMIND`, `--company ROBROS`, `--company "Figure AI"`로 조사하며, API 없는 `--demo`는 Figure AI를 자동 선택한다. 입력 위치는 `--data-dir`, `--com-data-dir`, `--index-dir`로 지정하며 원본 프로젝트가 없으면 이 폴더의 복사본을 쓴다. 첫 실행은 BGE 모델 다운로드·임베딩이 필요하며 이후 인덱스를 재사용한다.

```bash
.venv/bin/python app.py --combine-reports output  # 저장된 3기업 결과로 최종 선정·통합만 실행
.venv/bin/python app.py --combine-reports output --reuse-analysis output/combined_analysis.json
```

`--reuse-analysis`는 원보고서 SHA·기업 trace/상태·21항목 점수/사유·검증 주장·원문 페이지/출처가 같은 저장 합성·선정만 재사용하며 품질은 항상 새로 평가한다. `combined_analysis.json`은 품질·PDF 출력 전에 저장해 실패 복구 시 두 분석 모델을 다시 호출하지 않는다. 조건과 이전 형식의 제한은 [제출 재현 안내](SUBMISSION.md)를 따른다.

기본 실행의 최종 제출물은 `output/final_investment_report.pdf` 한 파일이다. A4·20mm 여백·전체 최대 10쪽 안에 3기업의 21항목 비교, 기업별 근거·위험, 최종 선택/보류·필요 조건과 출처를 담는다. 같은 이름의 Markdown·JSON은 검토용이며, 품질·규격 미달 PDF는 최종본으로 발행하지 않는다.

최신 실제 v10은 **하나의 A4 8쪽 보고서·Figure AI 조건부 투자**를 출력했다. 원문 검토를 마친 교정 본문과 동일하며, 검증된 합성·선정 재사용 후 새 Hybrid 평가에서 핵심 3축과 세 기업 분석·한계·개별 의견 반영이 모두 통과했다. 해당 실행의 수정은 0회이며, 최신 회귀 검사는 **428개 + 하위 테스트 22개 통과**다.

현재 검증 파일: [최종 PDF](output/submission-live/final_investment_report.pdf) · [본문](output/submission-live/final_investment_report.md) · [점수·반영 맵·품질 JSON](output/submission-live/final_investment_report.json).

기존 PNG 1~3은 **보고서 합성 단계 추가 전**의 실행 기록이며 최신 결과와 구분한다. 공식 파일·실행 캡처와 패키지 정보는 [제출 재현 안내](SUBMISSION.md)에서 확인한다.

기업별 `<company>_brief.md/.pdf`는 각 한 쪽 검토용 중간 요약이다. 최종 합성·선정은 `.state.json`의 **완성 보고서 전체**와 검증 원문·21항목 점수·판단을 자동 입력받는다. `--combine-reports DIR`은 워커 검색·임베딩·등급 판정 없이 합성→선정→통합 품질 평가를 실행한다.

`--revalidate-report STATE`는 저장된 검증 사실만 원문 재검증·재채점하고 보고서를 작성·평가하며, 필요한 재작성은 최대 두 번이다. 새 워커 검색은 없으며, 미래 계획을 현재 성과로 오인한 기존 출력을 교정하는 데 사용한다. 복구·테스트·패키지 생성은 [제출 재현 안내](SUBMISSION.md)를 따른다.

**LangSmith:** `.env`의 `LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`, `LANGSMITH_PROJECT`로 연결한다. 키가 없으면 로컬 JSON만 저장한다. 제출용 PNG는 실제 LangSmith 실행 화면에서 캡처하며 로컬 trace로 대체하지 않는다.

![실제 ROBROS 실행: 세 워커와 Synthesizer 합류 후 재계획](docs/traces/01_orchestrator_plan.png)

![실제 최종 선정: Figure AI 조건부 투자와 원문 근거·코드 보호 조건](docs/traces/02_final_investment_decision.png)

[실제 실행·캡처 기록](docs/traces/README.md): 01은 기업별 계획·재조사, 02~[03](docs/traces/03_final_quality.png)은 합성 전 결과다. [04 — 미달·재작성 루프](docs/traces/04_feedback_loop.png)는 중간 품질 실패·재작성 2회 진입, [05 — 최신 품질 통과](docs/traces/05_current_final_quality.png)는 검증된 분석 재사용 후 새 Judge의 3축 통과를 보여 준다.

## Contributors

- 이상수 : Source Research, Document Catalog — 기업 자료 조사·원문 수집·문서 카탈로그 관리
- 우강산 : PDF Parsing, Source Management — PDF 파싱·텍스트 추출·원본 페이지 및 출처 연결
- 정지영 : Embedding, Retrieval Agent — 임베딩·기업별 검색·검색 인덱스 및 캐시 관리
- 박소정 : Prompt Engineering, Analysis Agent Design — 빅테크·기술·시장 분석 워커 설계·분석 프롬프트 작성
- 김재혁 : Agent Orchestration, Investment Judge, Report Synthesis — LangGraph 제어·근거 검증·투자 판단·보고서 종합 및 품질 평가

제출 브랜치는 [`codex/physical-ai-agent`](https://github.com/fabianskii1/physical-ai-investment-rag/tree/codex/physical-ai-agent)이며 구현·최종 PDF·실제 LangSmith PNG·제출 ZIP을 함께 제공한다. ZIP 파일명 `Agent_{캠퍼스}_{X반}_{이름1+이름2+...}.zip`에 필요한 캠퍼스·분반만 미등록이므로 현재는 중립 파일명을 사용한다. 최종 제출 PDF는 **3기업 통합 한 파일, 전체 최대 10쪽**이다. [제출 재현 안내](SUBMISSION.md)에 패키지·복구 방법을 기록한다. 기업 운영 전에는 Judge 정답 사례 검증·평가 기준일·지속 복구·비용 상한을 보완해야 한다.
