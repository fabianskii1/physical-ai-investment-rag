# 최종 보고서 입력과 반영 검증

기업별 한 쪽 요약이 아니라 **기업별 완성 보고서 전문 + 21항목 평가 + 검증된 원문 페이지**를 최종 생성에 사용한다. 2026-10-16 대조에서 MOTOMIND 9,076자, ROBROS 9,900자, Figure AI 11,429자의 State 본문과 기존 최종 입력의 SHA256은 일치했다. 단순한 본문 키 누락은 아니었다.

## 실제로 확인한 문제

- 합성 없이 점수·긍정 사실만 결합하는 이전 함수 경로가 최종본을 발행할 수 있었다.
- 외부에서 전달한 `report_inputs`의 본문·해시·문단이 현재 State와 같은지 모든 최종 에이전트가 대조하지 않았다.
- `PortfolioOpinion.evidence_ids`는 선정 기업 전용이었다. 기업 간 비교 이유의 근거 필드가 없어 `comparison_reason`이 출처 없이 렌더링됐다.
- 요약·기업별 현재 의견의 인용 목록을 비워도 연결 검사를 통과했다. 실제 재실행에서 비교 문단·현재 의견의 인용 누락과 MOTOMIND의 성숙도를 낮춘 해석이 품질 미달로 검출됐다.
- 문단 ID 배열을 최대 6개로 제한했지만 세 기업 요약에 더 많은 문단을 연결하면서 여러 ID를 한 문자열로 묶은 실제 모델 출력이 발생했다. 최대 16개로 조정하고 각 문단·원문·최종 문단 ID에 개별 문자열 패턴을 적용했다. 잘못된 ID 문자열을 임의로 분해해 통과시키지 않는다.
- 출력 폴더에 풀어 둔 이전 제출 코드까지 pytest가 수집했다. `pytest.ini`로 현재 `tests/`만 검사한다.

첫 검증 실행은 `output/report-input-fix-20261016/`의 **품질 미달 초안**으로 보존한다. 통과한 최종본으로 취급하지 않는다.

두 번째 `output/report-input-fix-20261016-v2/`는 ID 목록 형식 오류로 합성 연결 검사에서 중단됐다. 수정한 문자열·배열 제약은 [OpenAI Docs의 Structured Outputs 지원 속성](https://developers.openai.com/api/docs/guides/structured-outputs)을 확인해 적용했다.

## 입력 계약

| 입력 | 최종 단계의 사용 위치 | 코드 검사 |
|---|---|---|
| `state.final_report` | `company_reports[].report_markdown` | 전문·SHA256·기업명·namespace·문단 인덱스; 내보낸 MD와 불일치 시 중단 |
| `research_result.source_checked_claims` | 회사별 `MM/RB/FG-E*`, `source_contexts` | 실제 PDF의 페이지와 인용문 일치 |
| `investment_result.score_details` | 공통 21항목 점수표·선정 판단 | 원 배점·등급·획득점수·U·저장 점수와 일치 |
| `CompanyReportSynthesis` | 기업별 5문단·요약·비교 2문단 | 실제 보고서의 분석·한계·의견 문단과 원문 인용 연결 |
| `PortfolioOpinion.evidence_ids` | 선정 기업의 사실·긍정 근거 | 선정 기업 전용 실제 E-ID |
| `PortfolioOpinion.comparison_evidence_ids` | 비교 이유 및 상대 비교를 포함한 선정 이유 | 세 기업의 실제 E-ID와 렌더링 연결 |
| `preservation_evidence` | Judge의 기업별 분석·한계·개별 의견 반영 판정 | 원기업 문단/발췌 ↔ 실제 최종 기업 구역의 문단/발췌 |

MD와 State가 다르면 한쪽을 임의로 선택하지 않는다. 변경한 기업 보고서와 근거를 같은 State·점수 묶음으로 저장한 뒤 합성한다. 원문이 부정하는 과장까지 복사하는 것이 아니라, 원보고서의 분석을 읽고 검증 원문의 범위 안에서 종합한다.

## API 없이 입력 확인

```bash
.venv/bin/python app.py --check-combine-inputs output/submission-live --output-dir output/input-check
```

`combined_report_inputs.json`에는 세 보고서 전문·문단·근거·점수·원본 페이지를 저장한다. 실제 생성도 동일한 준비 함수를 사용하며 최종 JSON의 `input_manifest`로 입력 파일과 SHA256을 연결한다. ZIP에서는 개인 경로 정규화 후 입력 파일의 해시를 다시 계산하고 원래 실행 해시도 보존한다.

```bash
.venv/bin/python app.py --combine-reports output/submission-live --output-dir output/new-final
```

검색·임베딩·재채점 없이 합성→선정→품질 평가를 실행한다. 비교 근거 필드 없는 이전 분석 캐시는 재사용하지 않는다.

## 그래프 연결

최종 합성·선정·품질·수정·PDF는 `final_report_pipeline.py`의 실제 StateGraph에서 실행한다. `app.py`가 기업 산출물을 전달하고 실행을 수집한다. [노드·분기·복구·실제 실패 처리](final_report_graph.md)를 참고한다.

## 품질 루프와 비용

문단별 출처와 스키마 오류는 가능한 한 합성/선정 연결 검사에서 잡는다. 보고서의 실제 표현·인용 결함은 최대 두 번 재작성한다. Judge 자신의 잘못된 반영 ID/발췌만 남은 경우에는 `action=review`로 초안을 보존하며 보고서 Writer를 다시 부르지 않는다. 새 근거가 필요하면 `research`로 종료한다. 동일 문서를 우연히 통과할 때까지 반복 평가하지 않는다.

반영 증거 검사는 기존 통합 Judge 호출 안에서 수행한다. 원기업의 U와 의견을 보존하고 관점 커버리지는 평가하지 않는다. 의미 보존은 LLM 평가이므로 정답셋 기반의 정확도·비용 절감률은 별도로 측정해야 한다.

## 실제 수정본 검증 결과

그래프 통합 전 2026-10-16 세 번째 실행은 합성·선정을 새로 수행해 A4 8쪽 최종본을 생성했고 재작성 0회로 품질을 통과했다. 당시 `output/report-input-fix-20261016-v3/`의 입력 전문과 최종 JSON에서 동일한 원기업 본문 해시, 비교 출처 필드, 실제 문단·발췌 증거 9개를 확인한다. root run은 `01a1425a-12f2-7ac3-8b8d-d5ea844c617c`다. 앞선 두 실패는 최종본으로 승격하지 않았다.
