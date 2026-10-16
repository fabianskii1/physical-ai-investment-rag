# 최종 보고서 오케스트레이션 그래프

기업별 `agent_pipeline.py`가 만든 보고서 전문·검증 State·점수를 `final_report_pipeline.py`의 `FinalReportState`로 전달한다. `app.py`는 최종 합성·선정 함수를 직접 순차 실행하지 않고 이 그래프를 실행한다. `--combine-reports`와 전체 배치가 같은 진입점을 사용한다.

## 노드와 의존 관계

| 실제 노드 | 입력 → 출력 |
|---|---|
| `prepare_final_inputs` | 기업 State → 본문·문단·SHA256·21항목 점수·검증 원문, 입력 전문 JSON |
| `report_synthesis` | 전체 기업 보고서·근거 → 회사별 해석·한계·현재 의견, 비교 분석·문단/인용 맵 |
| `portfolio_decision` | 같은 보고서·근거·합성 → 투자 대상·비교 근거·필수조건; 코드 보호 정책 적용 |
| `compose_final_report` | 합성·선정·기존 점수 → 고정 표·U·REFERENCE와 최종 본문; 저장 분석 |
| `final_quality` | 최종 본문·원기업 보고서·원문 → 세 품질 축과 실제 분석·한계·의견 반영 판정 |
| `final_orchestrator` | 현재 평가·수정 예산·입력 품질 → 수정 또는 출력 라우팅; 추가 LLM 없음 |
| `final_rewrite` | 같은 자료·본문·구체적 피드백 → 표현 수정; 점수·출처·공식 선정/조건 변경 거절 |
| `publish_final_report` | 평가된 마지막 안전 본문 → A4·동일 20mm·10쪽 이내 PDF 및 품질 기록 |
| `reuse_final_analysis` | 선택 옵션: 현재 입력과 해시/점수/원문이 같은 저장 분석 → 조립; 품질은 새로 평가 |

기업 보고서 PDF를 다시 OCR하지 않는다. 기업 PDF를 출력한 `state.final_report` 전문과 그 보고서가 근거로 사용한 검증 원문 PDF 페이지를 사용한다. 최종 LLM은 이 입력을 종합하는 노드에 있으며 새 조사·임베딩·등급 판정을 수행하지 않는다. LLM이 생성한 해석이 사실과 일치하는지는 원문 기반 Judge가 평가하고, 코드가 문단·인용·발췌·점수 일치를 검증한다.

## 피드백과 종료

- `rewrite`: `final_rewrite → final_quality → final_orchestrator`로 최대 2회 돌아간다. 기업 조사·합성·선정은 재호출하지 않는다.
- 무수정·잘못된 표/출처/공식 결론 변경: 수정본 거절 → Orchestrator로만 복귀한다. 같은 문서를 Judge에 반복 제출하지 않는다.
- `research`: 새로운 근거가 필요하므로 초안과 `final_report.research_request.json`을 남긴다. 기업 조사 자료를 보완한 뒤 새로 종합한다.
- `review`: Judge의 잘못된 반영 ID/발췌 같은 검토 문제는 초안으로 종료한다.
- 예산 소진·원기업 품질 미달·종합 누락: 최종본으로 승격하지 않고 초안을 보존한다.

상태 단계 한도 32, recursion 40으로 무한 루프를 막는다. 최종 그래프는 직렬 쓰기이므로 병렬 reducer가 필요 없다. PDF·벡터 바이트는 State에 넣지 않는다. 전체 State의 바이트 상한과 영구 DB 체크포인트는 구현하지 않았다.

## 추적과 복구

`final_report.trace.json`은 실제 실행 순서·노드 시간·기업 trace ID·graph UUID를, `final_report.graph.json`은 컴파일된 노드·조건 간선과 실행 경로를 기록한다. 최종 품질 JSON의 `graph_execution`에도 실행 경로가 들어간다. LangSmith에는 실제 LangGraph 노드가 표시되며 구조 이미지와 구분한다.

`InMemorySaver`로 같은 프로세스에서 PDF 노드 실패 후 `graph.invoke(None, config)` 재개 시 합성·선정·품질 모델을 다시 호출하지 않는 것을 테스트했다. 프로세스가 종료된 뒤에는 저장된 `combined_analysis.json`과 `--reuse-analysis`로 검증된 분석만 재사용하고 품질을 새로 평가한다.

## 검증

`tests/test_final_report_pipeline.py`는 전문·원문·점수 전달, 합성 분석의 선정 입력 연결, 피드백 중 원본 불변과 분석 재호출 방지, 새 근거 요청 종료, 무수정 예산 종료, 실패한 PDF 노드 체크포인트 재개, 분석 재사용 분기를 실제 컴파일 그래프로 검사한다. API 응답은 테스트에서 대체하며 실제 모델 실행 결과는 실행 trace·최종 품질 JSON에서 따로 확인한다.

그래프 API는 [LangGraph 공식 문서](https://docs.langchain.com/oss/python/langgraph/graph-api)의 StateGraph·조건 간선·체크포인트 방식을 따른다.

## 실제 그래프 실행에서 확인한 동작

첫 API 실행(`output/final-graph-20261016-live/`)은 새 합성·선정 → 품질 실패 → 본문 수정 → 재평가를 10개 노드 실행으로 수행했다. 수정본의 핵심 3축은 통과했으나 ROBROS의 limitations 증거로 kind=analysis 문단을 제출해 `review` 초안으로 종료했다. 통과로 승격하지 않았다. 이 실행에서 합성·선정은 각 1회만 실행했다.

그 혼동을 줄이기 위해 Judge 입력에 회사·검사 항목별 허용 원문 문단과 해당 회사의 최종 구역 문단을 `reflection_candidates`로 추가했다. 의미 반영은 여전히 Judge가 판정하며 유형·회사·발췌 검사도 유지한다. 잘못된 proof를 자동 교정하거나 검사 기준을 완화하지 않는다. 다음 실행은 동일 보고서·점수·원문·분석과 교정 본문을 검증해 품질부터 재개한다.

최종 재검증(`output/final-graph-20261016-verified/`)은 6개 노드·94.35초로 같은 분석·교정 본문을 읽어 품질을 통과하고 A4 8쪽 PDF를 출력했다. 핵심 3축·세 기업 반영이 모두 통과했고 반영 증거 9개를 대조했다. `analysis_reused=true`, 현재 실행 수정 0회이며 앞선 실행의 수정 1회를 숨기지 않는다. LangSmith root는 `01a1428a-1fc3-70a2-9cd4-b3c284078368`이다. 두 실제 root의 노드 순서를 SDK와 로컬 trace로 대조했으며 [감사 기록](../output/submission-live/final_report.execution_audit.json)에 원기업 전문·점수·선정·수정 본문 보존과 PDF 육안 검사 결과를 남겼다. 전체 회귀 검사는 477개 + 하위 테스트 22개를 통과했다.
