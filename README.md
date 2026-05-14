# Game Agent MVP

Windows에서 LDPlayer로 실행 중인 Android 게임을 ADB로 관찰하고 제어하기 위한 Python MVP입니다.

## Environment

- OS: Windows
- ADB: `C:\LDPlayer\LDPlayer9\adb.exe`
- Device: `emulator-5554`
- Python: 3.10+
- Dependency: `Pillow`, `ImageHash`, `opencv-python`

## Files

- `agent/env.py`: LDPlayer ADB 제어용 `GameEnvironment`
- `agent/main.py`: 스크린샷 저장 및 dry-run tap 테스트
- `agent/actions.py`: action JSON 검증 및 실행
- `agent/act.py`: 대화창에서 받은 action JSON을 ADB input으로 실행하는 CLI
- `agent/behaviors.py`: 게임 도메인 행동 정의
- `agent/run_behavior.py`: 이름 기반 행동 실행 CLI
- `agent/calibrate.py`: 좌표 격자 생성 및 target 좌표 저장 CLI
- `agent/target_config.py`: 행동 target 좌표 설정 로드/저장
- `agent/vision.py`: template matching 기반 화면 탐지 유틸
- `agent/stage.py`: 스테이지 숫자 ROI 저장 및 template 기반 인식
- `agent/stage_cli.py`: 스테이지 숫자 인식/템플릿 저장 CLI
- `ldplayer_mcp/server.py`: OpenCode에서 호출할 LDPlayer MCP 서버
- `requirements.txt`: Python dependency 목록

## Usage

LDPlayer가 실행 중이고 ADB device가 잡혀 있는지 확인합니다.

```cmd
C:\LDPlayer\LDPlayer9\adb.exe devices
```

스크린샷 저장 테스트를 실행합니다. 기본적으로 tap은 실행하지 않습니다.

```cmd
python -m agent.main
```

기본적으로 원본 스크린샷과 대화창 확인용 축소본을 함께 저장합니다.

```text
screenshots/latest.png
screenshots/latest_preview.png
screenshots/history/screen_YYYYMMDD_HHMMSS.png
screenshots/history/screen_YYYYMMDD_HHMMSS_preview.png
```

게임 조작 좌표는 원본 화면 기준입니다. 현재 권장 기준은 `360x640`입니다. 기본 preview는 사용자가 확인하기 쉽도록 원본 크기(`1.0`)로 저장합니다.

저장 위치를 바꾸려면 다음처럼 실행합니다.

```cmd
python -m agent.main --screenshot screenshots/test.png --preview screenshots/test_preview.png
```

축소 비율을 바꾸려면 다음처럼 실행합니다.

```cmd
python -m agent.main --preview-scale 0.4
```

현재 기본 preview 비율은 `1.0`입니다.

## Autonomous Explorer Stage 1

탐색기는 LLM 판단 없이 화면 캡처, 후보 좌표 선택, tap/back 실행, 전후 화면 변화 비교, 행동 로그와 state graph 저장을 수행합니다.

기본 설정은 `config.yaml`에 있습니다.

```yaml
adb_path: "C:\\LDPlayer\\LDPlayer9\\adb.exe"
device_id: "emulator-5554"
screenshot_dir: "runtime/screenshots"
actions_log: "runtime/actions.jsonl"
state_graph: "runtime/state_graph.json"
planner_decisions: "runtime/planner_decisions.jsonl"
planner_request: "runtime/planner_request.json"
planner_response: "runtime/planner_response.json"
learning_memory: "runtime/learning_memory.json"
goal_progress: "runtime/goal_progress.json"
current_goal: "explore_safely"
dry_run: true
safe_mode: true
state_hash_threshold: 6
```

주요 구현 파일:

- `tools/capture.py`: `adb exec-out screencap -p`로 `runtime/screenshots/current.png` 저장
- `tools/input.py`: `tap`, `swipe`, `back`, `wait` 제공
- `tools/candidates.py`: 고정 좌표 fallback, OpenCV contour, 밝은 UI 영역, 하단 메뉴 후보 생성
- `explorer/memory.py`: `runtime/actions.jsonl`에 `screen_hash`, `candidate_id`, `x`, `y`, `changed` 기록
- `explorer/learning_memory.py`: action 결과를 candidate feature pattern별로 누적하는 `runtime/learning_memory.json` 관리
- `explorer/screen_hash.py`: `imagehash.phash` 기반 perceptual hash 계산
- `explorer/state_graph.py`: `runtime/state_graph.json`의 states/edges 로드 및 저장
- `explorer/prompt_builder.py`: 현재 state, candidate 목록, 이미 시도한 action, 최근 edge 기반 planner prompt 생성
- `explorer/planner.py`: `BasePlanner` 인터페이스와 `MockPlanner` 구현
- `explorer/strategy.py`: 2단계용 단순 strategy 구현, 현재 runner는 planner를 사용
- `explorer/runner.py`: observe -> state 인식 -> planner action 선택 -> dry-run 출력 또는 실행 -> edge 기록 루프

`safe_mode: true`에서는 로그아웃, 게임 종료, 현금 결제 유도 버튼일 가능성이 큰 상단 버튼, 팝업 확인 버튼, 일부 우측 하단 후보를 건너뜁니다. 텍스트/OCR 없이 의미를 확정할 수 없는 버튼은 누르지 않는 쪽으로 처리합니다.

기본값은 dry-run입니다. 이 모드에서는 실제 입력을 보내지 않으며, 같은 후보를 실행 완료로 소비하지 않습니다.

```cmd
.venv\Scripts\python.exe -m explorer.runner
```

실제로 LDPlayer에 tap을 보내려면 `--execute`를 명시합니다.

```cmd
.venv\Scripts\python.exe -m explorer.runner --execute
```

여러 번 반복하려면 `--iterations`를 사용합니다. `0`은 중단할 때까지 계속 실행합니다.

```cmd
.venv\Scripts\python.exe -m explorer.runner --execute --iterations 10
```

생성되는 주요 파일:

- `runtime/screenshots/current.png`: 최신 화면
- `runtime/screenshots/000001_before.png`: 클릭 전 화면
- `runtime/screenshots/000001_after.png`: 클릭 후 화면, execute 모드에서만 생성
- `runtime/actions.jsonl`: `screen_hash`, 후보 좌표, 변화 여부, 실행 여부 로그
- `runtime/state_graph.json`: perceptual hash state와 action edge 그래프
- `runtime/planner_decisions.jsonl`: planner prompt 요약, 선택 action, reason 로그
- `runtime/learning_memory.json`: candidate feature pattern별 success/fail 학습 통계
- `runtime/goal_progress.json`: goal별 attempt/success/fail 및 signal 누적 통계

같은 화면의 같은 후보는 `runtime/state_graph.json`에 edge로 기록된 경우 다시 선택하지 않습니다. 현재 state의 모든 candidate가 시도되면 strategy가 `back` action을 반환합니다.

3단계 planner action schema:

```json
{
  "type": "tap_candidate",
  "candidate_id": "center",
  "reason": "MockPlanner selected the first candidate not yet tried from this state."
}
```

현재는 실제 LLM API를 호출하지 않고 `MockPlanner`가 prompt와 state graph를 받아 아직 시도하지 않은 첫 candidate를 선택합니다. 이후 OpenCode, MCP, 외부 LLM API 연결은 `BasePlanner.choose_action()` 구현만 교체하면 됩니다.

4B 단계에서는 파일 기반 external planner를 사용할 수 있습니다. 기본값은 `mock`입니다.

```cmd
.venv\Scripts\python.exe -m explorer.runner --planner external
```

`--planner external`은 `runtime/planner_request.json`을 생성하고 `runtime/planner_response.json`을 읽습니다. 응답 파일이 없으면 blocking하지 않고 안전하게 `wait` action을 반환합니다.

외부 planner가 응답을 작성할 시간을 주려면 semi-auto blocking loop를 사용할 수 있습니다.

```cmd
.venv\Scripts\python.exe -m explorer.runner --planner external --wait-for-response --response-timeout 300 --clear-response-after-use
```

옵션:

- `--wait-for-response`: `planner_response.json`이 생길 때까지 1초마다 확인
- `--response-timeout SEC`: 응답 대기 최대 시간, 기본 `300`
- `--clear-response-after-use`: 응답을 action으로 읽은 뒤 `planner_response.json` 삭제

오래된 response 파일을 수동으로 지우려면 다음을 실행합니다.

```cmd
.venv\Scripts\python.exe scripts\clear_planner_response.py
```

`planner_request.json`에는 현재 state, 스크린샷, candidates debug 이미지, candidate 목록, 이미 시도한 candidate, 최근 edge, 목표, action schema가 포함됩니다.
각 request에는 stale response를 막기 위한 `request_id`가 포함됩니다.

`planner_response.json` 형식:

```json
{
  "request_id": "20260513T031835.123456Z_state_000001_ab12cd34",
  "type": "tap_candidate",
  "candidate_id": "bottom_menu_1",
  "reason": "Open a lower navigation tab to identify its screen."
}
```

`candidate_id`가 현재 후보 목록에 없거나 `request_id`가 현재 request와 다르면 runner는 `wait`으로 fallback합니다. 모든 planner 결정은 계속 `runtime/planner_decisions.jsonl`에 기록됩니다.

5A 단계에서는 별도 터미널에서 `planner_bridge.py`를 실행해 `planner_request.json`을 자동으로 `planner_response.json`으로 변환할 수 있습니다. 현재는 실제 LLM/OpenCode 호출 없이 `MockBridge`가 candidates 중 아직 시도하지 않은 후보를 선택합니다. 후보가 없으면 `wait`, 모든 후보가 이미 시도됐으면 `back`을 작성합니다.

터미널 1: explorer 실행

```cmd
.venv\Scripts\python.exe -m explorer.runner --planner external --wait-for-response --clear-response-after-use --execute --iterations 0
```

터미널 2: bridge 실행

```cmd
.venv\Scripts\python.exe planner_bridge.py --watch --poll-interval 1
```

한 번만 처리하려면 다음처럼 실행합니다.

```cmd
.venv\Scripts\python.exe planner_bridge.py --once
```

`planner_bridge.py`는 request 파일의 modified time과 content hash를 기억해 같은 request를 반복 처리하지 않습니다. runner와 bridge는 서로 다른 프로세스로 동작하며, 파일 기반 protocol만 공유합니다.

5B 단계부터 `MockBridge`는 단순 raw score 최대값 대신 `adjusted_score`로 후보를 정렬합니다. 기본 score에 일반 UI 탐색용 kind 가중치, bbox 크기 penalty, 상단/모서리 위치 penalty, 하단 진행 버튼 bonus, `runtime/actions.jsonl`의 과거 changed 결과 기반 penalty/bonus를 합산합니다.

kind 가중치:

- `bottom_menu`: `+0.20`
- `contour`: `+0.15`
- `fixed`: `+0.05`
- `bright_region`: `-0.10`

과거 결과 반영:

- 같은 state 또는 screen hash에서 이미 시도한 candidate는 제외
- `bottom_menu_N`과 `bottom_nav_N`은 같은 하단 슬롯 alias로 보고 이미 시도한 슬롯을 제외
- 같은 candidate가 과거 `changed=false`였으면 강한 penalty
- 같은 kind에서 `changed=false`가 누적되면 penalty
- 같은 kind에서 `changed=true`가 있었으면 작은 bonus
- `runtime/learning_memory.json`에서 같은 feature pattern의 `success_count`가 높으면 bonus
- 같은 feature pattern의 `changed_false_count`/`fail_count`가 반복되면 penalty

디버그 이미지 기준 조정:

- `y < 80` 상단 상태바/재화 영역 후보에는 강한 penalty 적용
- 화면 하단 55~85% 영역의 적당히 큰 `contour` 후보에는 `progress_button_bonus` 적용
- 너무 작은 `bright_region` 후보에는 일반 작은 bbox보다 강한 penalty 적용

선택 시 bridge 콘솔에는 상위 10개 후보의 `raw_score`, `adjusted_score`, bbox, penalty/bonus breakdown이 출력됩니다. 선택된 response reason에도 kind, bbox, adjusted score와 주요 조정 이유가 포함됩니다. 모든 후보가 제외되거나 최고 `adjusted_score`가 기준보다 낮으면 `back`을 선택합니다.

추가 옵션:

```cmd
.venv\Scripts\python.exe planner_bridge.py --watch --min-adjusted-score 0.05 --prefer-exploration
```

```cmd
.venv\Scripts\python.exe planner_bridge.py --watch --prefer-safe
```

`--prefer-exploration`이 기본값입니다. `--prefer-safe`는 작은 bbox, 상단/모서리 위치, 실패 패턴에 대한 penalty를 더 강하게 적용합니다.

5E 단계에서는 action outcome을 일반화한 learning memory layer를 사용합니다. 게임 룰이나 텍스트를 하드코딩하지 않고 candidate feature pattern과 실행 결과만 저장합니다.

feature pattern 구성:

- `kind`: `contour`, `popup_button`, `back`, `repair_tap` 등 action/candidate 종류
- `layer`: `normal` 또는 `modal`
- `group_or_parent`: modal/group/parent 식별자, 없으면 `none`
- `relative_position_bucket`: 화면 내 상대 위치 bucket, 예: `lower_mid_right`
- `bbox_size_bucket`: `none`, `tiny`, `small`, `medium`, `large`, `huge`

`runtime/learning_memory.json` 형식:

```json
{
  "version": 1,
  "patterns": {
    "popup_button|modal|modal_203_407_300x111|lower_mid_right|medium": {
      "features": {
        "kind": "popup_button",
        "layer": "modal",
        "group_or_parent": "modal_203_407_300x111",
        "relative_position_bucket": "lower_mid_right",
        "bbox_size_bucket": "medium"
      },
      "success_count": 1,
      "fail_count": 2,
      "changed_true_count": 1,
      "changed_false_count": 2,
      "modal_dismiss_success_count": 0,
      "last_outcome": {
        "changed": false,
        "success": false,
        "modal_dismiss_success": false,
        "before_state_id": "state_000018",
        "after_state_id": "state_000018"
      }
    }
  }
}
```

학습 업데이트 규칙:

- `changed=true`는 기본 success 신호로 저장
- `changed=false`는 fail 및 changed_false 누적으로 저장
- `active_layer=modal`에서 `back` 후 modal layer가 사라지면 `modal_dismiss_success_count` 증가
- runner는 action 실행 후 `learning_memory.json`을 업데이트
- `planner_request.json`과 OpenCodeBridge prompt에는 `learning_memory_summary`가 포함됨
- MockBridge는 `learning_success_pattern_bonus`, `learning_repeated_false_pattern_penalty`, `learning_fail_dominant_pattern_penalty`를 adjusted score에 반영

5F 단계에서는 goal/task layer로 단순 탐색이 아니라 목표 지향 탐색을 지원합니다. 현재 목표는 `config.yaml`의 `current_goal`로 지정합니다.

기본값:

```yaml
current_goal: "explore_safely"
goal_progress: "runtime/goal_progress.json"
```

goal schema:

```json
{
  "goal_id": "dismiss_modal",
  "description": "Dismiss the active modal safely without confirming exit or destructive choices.",
  "priority": 10,
  "success_signals": ["modal_dismissed", "active_layer_changed_from_modal", "changed_true"],
  "avoid_signals": ["modal_still_active", "confirm_or_exit_candidate", "repeated_changed_false"],
  "preferred_candidate_kinds": ["popup_button", "fixed", "back"],
  "preferred_layers": ["modal"]
}
```

내장 goal:

- `explore_safely`: 안전 탐색 기본 목표
- `dismiss_modal`: active modal에서 cancel/right/close/back-style dismiss 우선
- `find_progression`: 큰 contour/button, highlighted action, 하단 진행 영역 우선
- `collect_rewards`: 밝거나 reward-like 후보 우선, purchase/exit risk 회피
- `explore_menu`: bottom menu 후보 우선, learning memory의 성공/실패 반영
- `inspect_mercenary_synergy`: 하단 좌측 2번째 용병 탭 진입 후 작은 synergy icon/detail 후보를 신중하게 조사

goal 반영 위치:

- runner가 `current_goal`을 resolve해서 planner에 전달
- `planner_request.json`에 `current_goal` 포함
- OpenCodeBridge prompt에 `current_goal`, `success_signals`, `avoid_signals` 포함
- MockBridge adjusted score에 `goal_*` bonus/penalty 포함
- action/repair 결과가 `runtime/goal_progress.json`에 goal별로 누적

`runtime/goal_progress.json` 예시:

```json
{
  "version": 1,
  "goals": {
    "dismiss_modal": {
      "attempt_count": 3,
      "success_count": 1,
      "fail_count": 2,
      "success_signals_seen": {"modal_dismissed": 1},
      "avoid_signals_seen": {"modal_still_active": 2},
      "recent_attempts": [
        {
          "action_type": "back",
          "candidate_id": null,
          "changed": true,
          "active_layer_before": "modal",
          "active_layer_after": "normal",
          "success_signals": ["changed_true", "modal_dismissed"],
          "avoid_signals": []
        }
      ]
    }
  }
}
```

목표를 바꾸려면 `config.yaml`에서 `current_goal`만 바꿉니다.

```yaml
current_goal: "find_progression"
```

용병/시너지 조사 목표:

```yaml
current_goal: "inspect_mercenary_synergy"
```

이 목표는 먼저 `bottom_menu_2`/`bottom_nav_2`를 강하게 선호합니다. 이후에는 일반 layer의 중앙 콘텐츠 영역에서 작은/중간 `contour` 또는 `bright_region` 후보를 synergy icon/detail 후보로 보고 우선하되, `learning_memory.json`에 쌓인 `changed=false` 실패 패턴은 계속 penalty로 반영합니다.

5C 단계에서는 실험적으로 OpenCode CLI를 호출하는 bridge를 사용할 수 있습니다. 기본값은 계속 `mock`이며, `--bridge opencode`일 때만 `opencode run` subprocess를 실행합니다.

```cmd
.venv\Scripts\python.exe planner_bridge.py --watch --bridge opencode --opencode-timeout 120 --opencode-cmd opencode
```

OpenCodeBridge는 `planner_request.json`의 요약, candidate 목록, `tried_candidates`, action schema, `debug_image_path`, 목표를 prompt로 만들어 `opencode run`에 전달합니다. OpenCode 출력에서 JSON action만 추출해 `planner_response.json`에 쓰며, response에는 항상 현재 `request_id`를 포함합니다.

OpenCodeBridge fallback 조건:

- `opencode` 실행 실패 또는 timeout
- `opencode run` non-zero exit
- 출력에서 JSON action을 파싱하지 못함
- action type이 schema와 다름
- 반환한 `candidate_id`가 현재 request candidates에 없음
- 반환한 `candidate_id`가 이미 tried/excluded 후보임
- 반환한 `request_id`가 현재 request와 다름

fallback 시에는 runner가 멈추지 않도록 로그를 남기고 같은 request에 대해 `MockBridge` 선택을 사용합니다. OpenCode가 이미지 파일을 vision으로 볼 수 없는 환경에서도 mock fallback이 유지됩니다.

5D 단계에서는 action 실행 후 `changed=false` 실패를 평가하고 geometry 기반 self-repair를 시도할 수 있습니다. repair는 planner/bridge가 아니라 runner 계층에서 수행합니다.

```cmd
.venv\Scripts\python.exe -m explorer.runner --planner external --wait-for-response --clear-response-after-use --execute --enable-repair --max-repair-attempts 5
```

평가 규칙:

- `changed=true`: success
- `changed=false` + `active_layer=modal` + `action.kind=popup_button`: `click_target_failed`
- `changed=false` + 같은 candidate 반복: `low_value_candidate`
- state가 바뀌지 않고 modal이 유지됨: `modal_not_dismissed`

repair 후보는 사람이 좌표를 주지 않고 modal/button geometry로 생성합니다.

- 원래 tap point 주변 offset
- modal 하단 오른쪽 cancel/no 영역 grid
- modal 상단 우측 close 영역
- detected `popup_button` bbox 내부 3x3 sample points
- 이전 성공 repair의 modal-relative position

repair 시도는 `actions.jsonl`에 `is_repair=true`, `repair_reason`, `repair_strategy`, `parent_candidate_id`, `repair_attempt_index`와 함께 기록됩니다. 성공한 repair는 `runtime/repair_memory.json`에 modal-relative position으로 저장되고, 이후 유사 modal의 safe candidate tap point 보정에 사용됩니다. `candidates_debug.png`에는 repair attempt point가 `R1`, `R2` 형태로 표시될 수 있습니다.

`planner_decisions.jsonl`의 `response_source` 값:

- `mock`: MockPlanner 결정
- `external_file`: 유효한 `planner_response.json` 결정
- `external_timeout`: 응답 파일 없음 또는 timeout
- `external_invalid`: JSON 오류, 잘못된 action type, 알 수 없는 candidate_id

4A 단계 Candidate Finder는 `runtime/screenshots/current.png` 또는 전달된 스크린샷을 읽어 다음 schema의 후보를 반환합니다.

```json
{
  "id": "contour_180_320_96x40",
  "x": 180,
  "y": 320,
  "kind": "contour",
  "layer": "normal",
  "score": 0.72,
  "bbox": [132, 300, 96, 40],
  "parent": null,
  "group": null
}
```

지원하는 `kind`:

- `fixed`: 기존 고정 좌표 fallback
- `contour`: OpenCV edge/contour 기반 UI 후보
- `bright_region`: 밝고 채도가 있는 강조 UI 영역
- `bottom_menu`: 화면 하단 메뉴 슬롯
- `popup`: 화면 중앙/하단 중앙의 큰 modal 영역
- `popup_button`: popup 내부의 취소/닫기/좌우 버튼 후보
- `modal`: active modal 영역

후보 생성은 먼저 `detect_active_layer(image)`로 `normal` 또는 `modal` layer를 감지합니다. `normal`에서는 contour, bright region, bottom menu, 낮은 우선순위 fixed fallback을 함께 생성합니다. `modal`에서는 background contour, bottom menu, 일반 fixed 후보를 제외하고 modal 영역과 modal 내부 버튼 후보만 반환합니다.
`planner_request.json`에는 `active_layer`가 포함됩니다. 각 candidate는 `layer`를 포함하고, `popup_button`은 `parent`/`group`으로 소속 modal id를 포함합니다. modal이 감지되면 bridge ranking과 OpenCode prompt 모두 modal layer 후보만 선택하도록 제한합니다.

`runtime/state_graph.json` 구조:

```json
{
  "states": [
    {
      "state_id": "state_000001",
      "hash": "ed69a352f2ca82e2",
      "first_seen": "2026-05-13T02:13:07.090213+00:00",
      "last_seen": "2026-05-13T02:13:07.090213+00:00",
      "screenshot_path": "runtime/screenshots/000001_before.png"
    }
  ],
  "edges": [
    {
      "from_state": "state_000001",
      "to_state": "state_000002",
      "action": {"type": "tap", "candidate_id": "center", "x": 180, "y": 320},
      "changed": true,
      "timestamp": "2026-05-13T02:14:00.000000+00:00"
    }
  ]
}
```

## Chat-Driven Control

최종 루프는 OpenAI Vision API를 코드에 붙이는 방식이 아니라, 사용자가 이 대화창에 LDPlayer 스크린샷을 보내고 assistant가 action JSON을 반환하는 방식입니다.

```text
1. python -m agent.main 으로 스크린샷과 preview 저장
2. assistant가 `screenshots/latest_preview.png`를 확인
3. assistant가 action JSON 반환
4. 반환된 JSON을 python -m agent.act 로 실행
5. 다시 스크린샷 저장 후 반복
```

assistant가 반환할 action JSON 형식은 다음 중 하나입니다.

```json
{"action":"tap","x":960,"y":820,"reason":"confirm button"}
```

```json
{"action":"swipe","x1":960,"y1":850,"x2":960,"y2":250,"duration_ms":500,"reason":"scroll down"}
```

```json
{"action":"back","reason":"close current screen"}
```

```json
{"action":"wait","seconds":1.5,"reason":"wait for loading"}
```

```json
{"action":"none","reason":"no safe action"}
```

기본 실행은 dry-run입니다.

```cmd
python -m agent.act --json "{\"action\":\"tap\",\"x\":960,\"y\":820}"
```

실제로 LDPlayer에 입력을 보내려면 `--execute`를 붙입니다.

```cmd
python -m agent.act --execute --json "{\"action\":\"tap\",\"x\":960,\"y\":820}"
```

## Behaviors

게임에서 반복적으로 쓰는 고수준 행동은 `behavior`로 분리합니다. `behavior`는 내부적으로 `tap`, `wait`, `swipe` 같은 primitive action들의 시퀀스를 반환합니다.

현재 구현된 행동:

- `enter_sewer`: 메인 화면의 `하수구 진입` 버튼을 누르고 잠시 대기
- `go_to_stage_249_and_enter`: 현재 300 스테이지 기준으로 좌측 화살표를 51번 클릭해 249 스테이지로 이동한 뒤 입장 버튼 클릭

기본 실행은 dry-run입니다.

```cmd
.venv\Scripts\python.exe -m agent.run_behavior enter_sewer
```

실제로 실행하려면 `--execute`를 붙입니다.

```cmd
.venv\Scripts\python.exe -m agent.run_behavior enter_sewer --execute
```

현재 기본 좌표는 `config/targets.json`에서 읽습니다. 실제 버튼 위치가 다르면 좌표를 덮어쓸 수 있습니다.

```cmd
.venv\Scripts\python.exe -m agent.run_behavior enter_sewer --x 125 --y 610 --execute
```

좌표를 다시 잡으려면 calibration grid를 생성합니다.

```cmd
.venv\Scripts\python.exe -m agent.calibrate --step 20
```

생성된 파일을 보고 버튼 중심 좌표를 정합니다.

```text
screenshots/calibration_grid.png
```

정한 좌표를 저장합니다.

```cmd
.venv\Scripts\python.exe -m agent.calibrate --set-target enter_sewer --x 125 --y 610
```

이후 `enter_sewer`는 저장된 좌표를 사용합니다.

`go_to_stage_249_and_enter`는 아래 target들이 confirmed 상태이고, 현재 스테이지 숫자를 이미지에서 읽을 수 있어야 실행됩니다.

- `stage_left_arrow`: 가운데에 떠있는 좌측 화살표 중심
- `stage_enter_button`: 입장 버튼 중심

좌표 저장 예시:

```cmd
.venv\Scripts\python.exe -m agent.calibrate --set-target stage_left_arrow --x 120 --y 320
.venv\Scripts\python.exe -m agent.calibrate --set-target stage_enter_button --x 180 --y 580
```

실행:

```cmd
.venv\Scripts\python.exe -m agent.run_behavior go_to_stage_249_and_enter --execute
```

현재 스테이지 읽기:

```cmd
.venv\Scripts\python.exe -m agent.stage_cli read --target 249
```

검증 포함 실행:

```cmd
.venv\Scripts\python.exe -m agent.stage_goal --target-stage 249 --execute
```

이 명령은 `현재 스테이지 읽기 -> 클릭 수 계산 -> 클릭 -> 다시 스테이지 읽기 -> 249일 때만 입장` 순서로 동작합니다.

스테이지 숫자 template 저장:

```cmd
.venv\Scripts\python.exe -m agent.stage_cli save-template 300
```

템플릿 저장은 실제 화면 숫자를 사람이 확인한 뒤에만 해야 합니다.

```cmd
.venv\Scripts\python.exe -m agent.stage_cli save-template 300 --confirmed
```

이제 이 behavior는 고정 51회 클릭이 아니라 `현재 스테이지 - 249`로 클릭 수를 계산합니다. 현재 스테이지를 읽지 못하면 실행하지 않습니다.

## Next Step

## OpenCode MCP

OpenCode Desktop에서 모델이 LDPlayer를 직접 다루게 하려면 로컬 MCP 서버를 붙입니다.

MCP 서버 실행 파일:

```text
C:\Development\Game_Agent\ldplayer_mcp\server.py
```

OpenCode MCP 설정 예시는 다음과 같습니다. 실제 config 위치와 포맷은 OpenCode Desktop 버전에 맞춰 조정해야 합니다.

```json
{
  "mcp": {
    "ldplayer": {
      "type": "local",
      "command": "C:\\Development\\Game_Agent\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Development\\Game_Agent\\ldplayer_mcp\\server.py"]
    }
  }
}
```

제공하는 MCP tools:

- `observe_screen(preview_scale, include_base64)`: 현재 화면 캡처, 이미지 경로/base64/해상도 반환
- `tap(x, y)`: ADB tap
- `swipe(x1, y1, x2, y2, duration_ms)`: ADB swipe
- `back()`: Android back
- `wait(seconds)`: 대기
- `run_behavior(name)`: 고수준 행동 실행
- `set_target(name, x, y, description)`: 행동 target 좌표 저장
- `read_stage_number(target_stage)`: 현재 스테이지 숫자 인식 및 목표까지 클릭 수 계산
- `save_stage_number_template(stage)`: 현재 스테이지 숫자 ROI를 template으로 저장
- `navigate_to_stage_and_enter_tool(target_stage)`: 현재 스테이지 인식, 목표 이동, 목표 확인 후 입장
- `list_behaviors()`: 사용 가능한 behavior 목록

첫 검증 프롬프트:

```text
ldplayer observe_screen 도구를 호출해서 현재 화면을 관찰하고, 이미지 경로 또는 base64를 실제로 볼 수 있는지 말해줘. 클릭은 하지 마.
```

이미지를 볼 수 있으면 다음 단계는 다음 프롬프트입니다.

```text
observe_screen으로 화면을 보고, 하수구 진입 버튼의 중심 좌표를 추정해. 클릭하지 말고 set_target으로 enter_sewer 좌표만 저장해.
```

좌표가 확인된 뒤에만 실행합니다.

```text
run_behavior enter_sewer를 실행해.
```

이 MVP가 안정적으로 동작하면 `run_behavior`가 고정 좌표 대신 template matching/OCR/상태 인식을 사용하도록 확장합니다.
