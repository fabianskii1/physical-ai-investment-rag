# 제출 자료 재현 안내

최종 제출 보고서는 [final_investment_report.pdf](output/submission-live/final_investment_report.pdf) 한 파일입니다. 세 기업의 공통 21항목 비교, 근거·위험, 최종 투자 검토 의견과 필요한 조건을 담으며 전체가 A4 최대 10쪽이어야 합니다. 같은 이름의 [Markdown](output/submission-live/final_investment_report.md)·[JSON](output/submission-live/final_investment_report.json)과 기업별 점수·실행 trace는 검토용 보조 자료입니다.

기업별 `motomind_brief`, `robros_brief`, `figure_ai_brief` PDF는 각 A4 한 쪽의 검토용 중간 산출물입니다. 최종 코드는 `.state.json`에 보존된 **기업별 완성 보고서 전체**·검증 State·점수를 실제로 읽습니다. `company_report_inputs.py`가 전체 본문·SHA256·문단 ID를 만들고, `report_synthesis.py`가 기업별 5필드 분석과 비교 2문단을 합성합니다. 같은 합성과 원보고서를 최종 선정 에이전트에 전달한 뒤 고정 점수표·출처와 합성 본문을 결합합니다. 원래 상세 PDF 경로는 metadata로 남기며 ZIP에서는 제외합니다. `MANIFEST.json`에 최종 보고서·한 쪽 요약·State와 제외된 상세 보고서의 경로 맵이 있습니다.

원보고서 문단 ID↔합성 본문↔원문 E-ID의 반영 맵을 기록합니다. 분석 출처 `report_paragraph_ids`와 실제 원문 인용 위치 `evidence_report_paragraph_ids`는 별도로 대조합니다. 원보고서의 과장은 검증 State·원문을 우선해 교정하며, 같은 품질 Judge 호출에서 핵심 3축과 별도로 분석·한계·현재 기업 의견의 반영 여부를 검사합니다. 길이·문장 완결성 검사와 `report_language.py`가 명백한 절단·자료 부재 단정·독립 입증 과장을 거절합니다. 실제 본문 문단의 인용↔원문 페이지를 대조해 RL·MOU 목적·IFR 수요·공공 실증의 알려진 범위 오류를 검사합니다. 이 검사는 제한된 오류 유형을 다루며 LLM Judge와 독립 원문 검토를 함께 사용합니다. 관점 커버리지는 평가에서 제외합니다.

**최신 실제 v10은 하나의 A4 8쪽 보고서·Figure AI 조건부 투자를 출력했습니다.** 원문 검토를 마친 교정 본문과 동일하며, 검증된 합성·선정 재사용 후 새 Hybrid 평가에서 핵심 3축과 세 기업 분석·한계·개별 의견 반영이 모두 통과했습니다. `issues=[]`, 이 실행의 수정은 0회입니다. 공식 경로의 PDF·본문은 v10과 같은 바이트이며 A4·20mm 여백·전체 8쪽을 확인했습니다. 중간 LLM 통과 뒤에도 독립 원문 대조에서 인용 오류를 발견해 수정했으므로 이전 통과를 최종 정확성 보장으로 사용하지 않습니다. PNG 1~3은 합성 전의 과거 실행이며, PNG 04는 중간 품질 미달·재작성, [PNG 05](docs/traces/05_current_final_quality.png)는 최신 새 Judge 통과입니다. 최신 회귀 검사는 **428개 테스트와 22개 하위 테스트 통과**를 확인했습니다. 실제 root run은 `01a134e2-45e8-75d3-b50c-b2246ae727ad`이며 SDK의 완료·오류 없음과 업로드를 확인했습니다.

`docs/architecture.png` 또는 `.svg`에는 구현 구조도를, `docs/traces/*.png`에는 실제 LangSmith 실행 화면을 보관합니다. 예시 화면과 로컬 trace JSON을 실제 LangSmith PNG 대신 사용하지 않습니다. 패키지 검사는 파일·품질 통과 기록·규격·해시를 확인하며 PNG의 실행 진위는 자동 판별하지 않습니다.

## 코드 실행

ZIP을 풀고 `physical_ai_orchestration` 폴더에서 실행합니다. Python 3.12와 한글 TrueType 폰트가 필요합니다. 시스템 한글 폰트가 없으면 `RAG_PDF_FONT`에 사용할 폰트 경로를 입력합니다.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-rag.txt
cp .env.example .env               # .env가 없을 때만
# .env에 본인의 OPENAI_API_KEY, LANGSMITH_API_KEY 입력
.venv/bin/python app.py --check-criteria
.venv/bin/python app.py --check-tracing
env LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false .venv/bin/python -m pytest -q
.venv/bin/python app.py --company all --output-dir output/submission-live \
  --data-dir rag/data --com-data-dir com_data --index-dir .cache/rag_index
# ZIP에 보존된 최종 State로 통합 보고서만 재생성하려면:
.venv/bin/python app.py --combine-reports output/submission-live
# 동일 입력으로 저장된 합성·선정만 재사용하고 품질은 새로 평가:
.venv/bin/python app.py --combine-reports output/submission-live \
  --reuse-analysis output/submission-live/combined_analysis.json
```

첫 실제 실행에는 공개 BGE-M3 모델 다운로드와 OpenAI 호출이 필요합니다. 계정 키와 모델 가중치는 ZIP에 포함하지 않습니다. 회사 자료집은 조사 단서이고, 등급은 `rag/data/catalog.csv`의 원문 PDF·등록 원문 및 평가 기준 PDF를 통해 검증합니다. 원문 회사 발표와 독립 확인, 미확인 U와 0등급을 구분합니다. `--demo`는 API 없는 구조 시연이며 제출용 실제 분석을 대체하지 않습니다.

현재 `.env.example`은 계획·워커 `OPENAI_MODEL=gpt-4.1-mini`, 기업별 Writer·원문·채점·품질 평가 `gpt-4.1`, 최종 합성·선정·통합 반영 Judge `gpt-5.4`로 분리합니다. 최종 역할은 `OPENAI_SYNTHESIS_MODEL`·`OPENAI_PORTFOLIO_MODEL`·`OPENAI_COMBINED_JUDGE_MODEL`로 변경합니다. 합성·통합 수정의 미지정 설정은 Writer→기본 모델, 통합 Judge는 기업별 Judge→기본 모델로 대체합니다. 모델 변경의 품질·비용 개선 효과는 미측정이며 API 계정에서 사용할 모델과 키를 설정해야 합니다.

`--combine-reports`는 저장된 실제 기업 보고서·State·점수를 사용하므로 검색·임베딩·등급 판정을 다시 하지 않습니다. 합성 LLM 기본 1회가 추가되고 연결 형식 오류는 1회만 교정합니다. 합성·최종 선정·통합 품질 평가에는 본인의 OpenAI 키가 필요합니다. 비용·품질 개선률은 아직 측정하지 않았습니다.

`--reuse-analysis JSON`은 `--combine-reports DIR`과 함께 사용합니다. 원보고서 SHA256, 기업 실행 ID·상태·총점, 21항목 점수·사유, 등록된 검증 주장, 원문 전체 페이지·제목·발행자·URL이 현재 입력과 일치해야 합니다. 파일 이동을 위한 `source_path` 차이만 허용합니다. 항목별 snapshot이 없는 이전 JSON은 같은 이름의 Markdown 고정 점수표로 점수만 대조하며 사유의 별도 대조는 제공하지 않습니다. 원보고서 SHA 검사는 별도로 유지합니다. 입력이 바뀌면 재사용을 거절하고 일반 합성·선정 경로로 실행해야 합니다.

분석은 품질·PDF 출력 전에 `combined_analysis.json`으로 저장합니다. 재사용 시 합성·선정 호출은 건너뛰지만 원문 기반 품질 판정은 새로 실행하며, 새 검색·재채점은 하지 않습니다. 같은 이름의 교정 Markdown이 있으면 고정 점수표·REFERENCE·공식 결론을 확인해 수정 본문부터 재평가하므로 초기 합성 문장으로 돌아가지 않습니다. 최종 Markdown 해시도 저장·대조하며 이전 품질 통과 값을 재사용하지 않습니다.

```bash
.venv/bin/python app.py --demo
.venv/bin/python app.py --resume-report output/submission-live/기업명.failed_state.json
# 같은 보고서·등급을 유지하고 내용 품질만 다시 평가:
.venv/bin/python app.py --review-report output/submission-live/기업명.state.json
# 이미 채택한 원문 근거를 재검증·재채점한 뒤 새 보고서 생성:
.venv/bin/python app.py --revalidate-report output/submission-live/기업명.state.json
```

품질 통과 뒤 PDF 출력만 실패한 상태는 모델 호출 없이 같은 Markdown을 다시 렌더링합니다. 조사 종료 후 Writer 실패 상태는 저장된 근거·점수를 유지하며 보고서 단계만 재개합니다. 일반 조사 중 프로세스 종료의 지속 복구는 지원하지 않습니다.

`--review-report`는 조사 결과·등급·예산·본문을 유지하면서 원문 품질 평가만 다시 실행합니다. `--revalidate-report`는 이전에 실제 채택한 원문 페이지만 재검증하며 새 검색·임베딩·워커 조사를 실행하지 않습니다. 새 검증 주장과 등급으로 이전 E-ID 연결을 교체하고, 원래 조사 예산을 유지한 채 새 보고서의 재작성 예산만 최대 2회로 시작합니다. 두 작업 모두 실제 OpenAI 평가 호출이 필요하며 통과 판정을 수동으로 덮어쓰지 않습니다.

## 패키지 생성

```bash
.venv/bin/python scripts/package_submission.py --preflight
.venv/bin/python scripts/package_submission.py
# 확정 정보를 받은 뒤 공식 파일명을 사용하려면 다음 항목을 입력합니다.
.venv/bin/python scripts/package_submission.py \
  --github-url 'https://github.com/fabianskii1/physical-ai-investment-rag/tree/codex/physical-ai-agent' \
  --campus '확정캠퍼스' --class-name '확정분반' --members '팀원1' '팀원2'
```

결과는 `output/submission/`의 ZIP, `manifest.json`, `archive.sha256`, `preflight.json`입니다. manifest의 SHA256은 ZIP에 실제로 들어간 각 파일을 검증합니다. 원본을 변경하지 않고 ZIP 복사본의 로컬 개인 경로를 상대경로로 정리합니다. `.env`·키·가상환경·모델 캐시·임시파일·과거 출력은 제외하며, 재현에 필요한 원천 PDF·기업 자료집·배점 PDF·코드·테스트를 포함합니다.

필수 최종 보고서·실제 trace PNG·구조도가 없거나 키·개인 경로가 남으면 ZIP을 새로 만들지 않습니다. 현재 패키지 사전 검사는 통과했으며 캠퍼스·분반만 미등록입니다. 미등록이면 중립 파일명을 사용하고 `pending_submission_fields`에 남깁니다. 그 ZIP은 검토용이며 공식 제출이 완료된 것으로 표시하지 않습니다.

현재 구현의 제출 링크는 [`codex/physical-ai-agent` 브랜치](https://github.com/fabianskii1/physical-ai-investment-rag/tree/codex/physical-ai-agent)입니다. 이 브랜치의 `minimal-orchestration/`에 실행 코드와 근거·보고서·PNG를, `submission/`에 ZIP·manifest·SHA256을 제공합니다. [기존 RAG의 main 브랜치](https://github.com/fabianskii1/physical-ai-investment-rag/tree/main)는 유지합니다.
