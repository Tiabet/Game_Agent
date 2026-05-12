# Game Agent MVP

Windows에서 LDPlayer로 실행 중인 Android 게임을 ADB로 관찰하고 제어하기 위한 Python MVP입니다.

## Environment

- OS: Windows
- ADB: `C:\LDPlayer\LDPlayer9\adb.exe`
- Device: `emulator-5554`
- Python: 3.10+

## Files

- `agent/env.py`: LDPlayer ADB 제어용 `GameEnvironment`
- `agent/main.py`: 스크린샷 저장 및 dry-run tap 테스트
- `agent/actions.py`: action JSON 검증 및 실행
- `agent/act.py`: 대화창에서 받은 action JSON을 ADB input으로 실행하는 CLI
- `requirements.txt`: 현재 외부 패키지 없음

## Usage

LDPlayer가 실행 중이고 ADB device가 잡혀 있는지 확인합니다.

```cmd
C:\LDPlayer\LDPlayer9\adb.exe devices
```

스크린샷 저장 테스트를 실행합니다. 기본적으로 tap은 실행하지 않습니다.

```cmd
python -m agent.main
```

저장 위치를 바꾸려면 다음처럼 실행합니다.

```cmd
python -m agent.main --screenshot screenshots/test.png
```

실제 tap 입력을 보내려면 명시적으로 `--tap`을 추가합니다.

```cmd
python -m agent.main --tap --tap-x 500 --tap-y 500
```

## Chat-Driven Control

최종 루프는 OpenAI Vision API를 코드에 붙이는 방식이 아니라, 사용자가 이 대화창에 LDPlayer 스크린샷을 보내고 assistant가 action JSON을 반환하는 방식입니다.

```text
1. python -m agent.main 으로 스크린샷 저장
2. 스크린샷을 이 대화창에 업로드
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

## Next Step

이 MVP가 안정적으로 동작하면 다음 단계에서 스크린샷 업로드와 action 실행을 더 자동화하는 래퍼를 붙입니다.
