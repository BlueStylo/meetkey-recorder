# MeetKey Recorder Project Index

Created: 2026-06-22
Project root: `/Users/gunwoo/CODEX_ORGANIZED/001_Active/06_MeetKey_Recorder`
Product name: `MeetKey`
Suggested repo/package name: `meetkey-recorder`
Korean working name: `MeetKey 회의록 녹음기`

## 1. Project Summary

MeetKey는 라즈베리파이와 디스플레이를 중심으로 만든 회의실용 보안 녹음/회의록 장치이다.

회의 시작 때 물리 토글 또는 화면 버튼을 누르면 장치가 새 회의 세션을 만들고, 화면에는 `녹음중` 상태와 일회용 QR 코드를 표시한다. 참석자가 QR로 접속하면 실시간 전사 텍스트를 볼 수 있다. 회의 종료 시 토글을 다시 누르면 녹음과 STT가 종료되고, 회의록 요약이 생성되며, QR 세션은 약 5분 뒤 만료된다.

핵심 가치는 다음과 같다.

- 회의실에 놓고 바로 쓰는 독립형 녹음/전사 장치
- QR 기반 일회용 접근으로 보안성과 사용 편의성 확보
- 실시간 전사, 회의 종료 후 요약, 액션아이템 추출
- 가능하면 로컬 또는 사내망 기반으로 민감한 회의 내용을 외부에 덜 노출

## 2. Recovered Context From Previous Codex Threads

이 프로젝트는 이전 Codex 대화에서 다음 맥락으로 시작되었다.

### Thread: 다중 마이크 STT 파악

사용자 구상:

```text
회의용 회의록 요약 장치
라즈베리파이로 구동
디스플레이 하나 장착
회의 시작 때 상단 토글 버튼 ON
디스플레이에 "녹음중" 표시
일회용 QR 생성
QR 접속 시 말하는 내용이 실시간 텍스트로 표시
회의 종료 때 토글 버튼 OFF
녹음 종료
QR은 5분 뒤 삭제/만료
보안 좋은 회의실 녹음기
```

그 대화에서 다중 마이크와 STT 구조에 대해 정리한 핵심은 다음과 같다.

- 8개 마이크 회의 녹음기는 보통 `8개 파일을 각각 STT`하는 방식이 아니다.
- 일반적인 회의실 마이크 어레이는 여러 마이크 입력을 이용해 더 깨끗한 하나의 오디오 스트림을 만든 뒤 STT에 넣는다.
- 다중 마이크를 쓰는 이유는 화자별 파일을 만들기 위해서라기보다, 빔포밍, 노이즈 제거, 반향 제거, 방향 추정, 먼 거리 음성 보강 때문이다.
- 단, 사람마다 개별 마이크가 하나씩 배정된 구조라면 채널별 STT가 유효할 수 있다.

대략적인 오디오 처리 개념:

```text
다중 마이크 입력
-> 동기화된 멀티채널 오디오
-> 노이즈 제거 / 에코 제거 / 음성 감지
-> 방향 추정
-> 빔포밍
-> 깨끗한 mono 오디오
-> STT
-> 화자 분리
-> 요약
```

초기 프로토타입 추천 방향:

- 1차 MVP는 단일 USB 마이크 또는 USB 회의용 마이크/스피커폰으로 시작한다.
- 다중 마이크 어레이는 이후 품질 개선 단계에서 검토한다.
- ReSpeaker 같은 4-mic, 6-mic, 8-mic 보드는 방향 추정과 빔포밍 실험에 적합할 수 있다.

### Thread: 회의록 전사 구성 확인

이전 로컬 AI 실험 맥락:

- 사용자는 `192.168.0.14` 서버, `speaker + whisperx + ollama 요약 모델` 조합을 기억하고 있었다.
- 확인된 흔적으로는 `192.168.0.14`가 Ollama 서버로 쓰였던 맥락이 있다.
- Mac 로컬에는 `Ollama + Open WebUI` 실행 흔적이 있었고, 로컬 모델로 `gemma4` 계열이 확인되었다.
- 과거 자동 전사 원본과 회의록 재정리 산출물도 있었다.
- 다만 WhisperX 실행 커맨드나 pyannote 설정 파일은 이전 확인에서 확정적으로 찾지는 못했다.

회의록 요약 파이프라인으로 정리했던 방향:

```text
녹음 파일
-> WhisperX 전사 / 타임스탬프
-> speaker diarization, 즉 화자 분리
-> Ollama 로컬 모델 요약
-> 회의록 Markdown / PPT / 액션아이템 출력
```

### Thread: 회의록 녹음기 폴더 경로 추천

프로젝트 루트는 정리된 작업 허브인 `CODEX_ORGANIZED` 아래에서 시작하기로 했다.

최종 폴더:

```text
/Users/gunwoo/CODEX_ORGANIZED/001_Active/06_MeetKey_Recorder
```

제품명은 `MeetKey`로 결정했다.

이름의 의미:

- `Meeting + Key`
- QR 기반 일회용 접근 키라는 프로젝트 특징을 담는다.
- 나중에 `MeetKey Device`, `MeetKey Live`, `MeetKey Summary`처럼 기능명을 확장하기 좋다.

## 3. Folder Map

```text
06_MeetKey_Recorder
  000_INDEX.md
  01_Research
  02_Source
  03_Experiments
  04_Hardware
  05_Recordings_Samples
  06_Docs
  90_Archive
  99_Temp
```

Folder purpose:

- `01_Research`: 마이크, 라즈베리파이, STT, 보안 세션, QR, 오픈소스 비교 조사
- `02_Source`: 실제 앱/서비스/펌웨어/스크립트 소스 코드
- `03_Experiments`: 날짜별 실험, 프로토타입, 실패/성공 로그
- `04_Hardware`: 부품 목록, 배선도, 케이스, 디스플레이, 버튼, 마이크 자료
- `05_Recordings_Samples`: 테스트용 녹음 샘플, 전사 결과, 품질 비교 자료
- `06_Docs`: 설계 문서, 화면 플로우, API 문서, 회의록 템플릿
- `90_Archive`: 더 이상 쓰지 않는 예전 설계나 산출물
- `99_Temp`: 임시 파일

## 4. Product Flow Draft

### Normal Meeting Flow

```text
Idle
-> user presses physical toggle or screen start button
-> create meeting session
-> create random one-time session token
-> generate QR code
-> start audio recording
-> start live STT or prepare batch STT
-> show "recording" state on device display
-> participants open QR URL
-> browser shows live transcript
-> user presses stop toggle
-> stop recording
-> finalize transcript
-> run summary
-> show complete state
-> expire QR after 5 minutes
```

### Device Screen States

1. `Idle`

   Device is waiting for a new meeting.

   Expected screen content:

   - Product name: `MeetKey`
   - Start control state
   - Microphone ready indicator
   - Network/server ready indicator
   - Optional storage remaining indicator

2. `Starting`

   Session is being created.

   Expected screen content:

   - Short loading state
   - Audio device check
   - QR/token generation
   - STT server connection check

3. `Recording`

   Main active meeting state.

   Expected screen content:

   - Large `녹음중` or recording indicator
   - Elapsed time
   - QR code
   - Session name or meeting ID
   - Optional waveform or level meter
   - Stop control

4. `Stopping`

   Recording is stopping and final processing is queued.

   Expected screen content:

   - `저장중` or `회의록 생성중`
   - Upload/transcription/summary progress if available
   - Avoid allowing duplicate stop/start actions

5. `Complete`

   Meeting is complete.

   Expected screen content:

   - `회의록 생성 완료`
   - QR still available for a short grace period
   - Countdown until QR expiry, initially planned as 5 minutes
   - Optional link status for summary/transcript

6. `Expired`

   Session QR has expired.

   Expected screen content:

   - `세션 만료`
   - Return to idle
   - Optional local admin-only recovery path if needed

7. `Error`

   Hardware or service problem.

   Expected error categories:

   - Microphone unavailable
   - Network unavailable
   - STT server unavailable
   - Storage full
   - QR/session server failure
   - Summary model failure

## 5. Architecture Draft

### Hardware Layer

Initial target:

- Raspberry Pi
- Display or touchscreen
- Physical top toggle button or reliable on-screen start/stop control
- USB microphone, USB conference mic, or later mic array
- Optional speaker/buzzer for feedback
- Local storage for raw recordings and logs

Prototype hardware recommendation:

- Start with a reliable USB microphone or USB conference speakerphone.
- Avoid starting with 8-channel custom mic handling unless audio quality becomes the main blocker.
- Add ReSpeaker or another mic array later if meeting-room distance/noise quality is poor.

### Device App Layer

Possible implementation choices:

- Local web app in kiosk mode on the Raspberry Pi display
- Python backend controlling audio capture and session state
- Browser frontend for device UI
- Hardware button event listener through GPIO
- Local HTTP/WebSocket server for QR clients

Core responsibilities:

- Manage session lifecycle
- Start/stop audio recording
- Generate QR token
- Display QR and recording state
- Serve live transcript page or proxy to backend
- Store recording metadata
- Trigger STT and summary pipeline
- Expire access tokens

### Audio Pipeline

MVP pipeline:

```text
USB mic
-> local WAV recording
-> batch STT
-> transcript
-> summary
```

Better later pipeline:

```text
USB conference mic or mic array
-> noise reduction / VAD
-> streaming STT
-> partial transcript
-> final transcript correction
-> diarization
-> summary
```

Advanced later pipeline:

```text
multi-channel mic array
-> beamforming
-> enhanced mono
-> streaming STT
-> diarization
-> summary
```

### STT and AI Layer

Candidate tools from previous context:

- WhisperX for transcription with timestamps
- Speaker diarization for separating speakers
- Ollama for local summary generation
- `gemma4` model family was seen in previous local setup
- `192.168.0.14:11434` was previously associated with an Ollama server

Possible result types:

- Full transcript
- Speaker-separated transcript
- Meeting summary
- Decisions
- Action items
- Follow-up questions
- Markdown report
- PPT-ready summary

## 6. Security and Privacy Notes

Security is a core product feature, not an afterthought.

Current intended behavior:

- QR should represent a random session URL or token.
- QR token should be unique per meeting.
- QR should expire after meeting end plus about 5 minutes.
- Expired QR should not expose transcript or audio.
- Public permanent links should be avoided.
- Raw audio should be deleted, encrypted, or explicitly retained according to a clear setting.
- For early prototypes, local network only is probably safer than internet exposure.

Potential access model:

```text
meeting starts
-> server creates session_id and random access_token
-> QR points to /session/:access_token
-> browser can read live transcript only while token is valid
-> meeting ends
-> token enters grace period
-> token expires after 5 minutes
-> session page becomes unavailable
```

Questions to resolve:

- Should summaries remain accessible after QR expiry for the host only?
- Does the device need an admin PIN or local dashboard?
- Should raw audio be saved by default?
- Should transcript be stored locally, uploaded to a server, or both?
- Should access be limited to same Wi-Fi/LAN?

## 7. MVP Proposal

Recommended first build should prove the full user loop before optimizing audio.

### MVP 1: Local Device Simulation

Goal:

Build a working local app that simulates the Raspberry Pi screen and meeting session flow on Mac.

Features:

- Device UI with idle/recording/complete states
- Start/stop button
- QR code generation
- Session token creation
- Fake or placeholder live transcript
- 5-minute expiry behavior, possibly configurable shorter for tests

### MVP 2: Recording

Goal:

Record audio from a microphone and save it with session metadata.

Features:

- Start recording on session start
- Stop recording on session stop
- Save WAV file
- Save metadata JSON
- Show elapsed time and level meter

### MVP 3: Batch Transcription

Goal:

After recording ends, transcribe the recorded file.

Features:

- Whisper/WhisperX integration
- Transcript output as Markdown or JSON
- Timestamps if possible
- Basic error handling

### MVP 4: Summary

Goal:

Generate a useful meeting summary.

Features:

- Send transcript to Ollama
- Generate summary
- Extract decisions and action items
- Save final Markdown meeting note

### MVP 5: Raspberry Pi Deployment

Goal:

Move working local prototype to Raspberry Pi.

Features:

- Kiosk display mode
- GPIO hardware toggle
- Autostart on boot
- Local network QR access
- Basic logs and recovery behavior

## 8. UI Notes For Future Session

The device screen should feel like an appliance, not a marketing landing page.

Design direction:

- Large readable status
- Very clear recording state
- QR code large enough to scan from a short distance
- Minimal controls during recording
- Avoid clutter
- Use color carefully: calm idle state, obvious recording state, clear completion state
- Show network/microphone readiness without making the screen technical

Likely screen layout:

```text
[MeetKey]                 [mic/network indicators]

          녹음중
          00:23:14

          [large QR code]

      실시간 회의록 보기
      QR은 종료 후 5분 뒤 만료됩니다

                 [Stop]
```

Browser page opened from QR:

- Session title or `MeetKey Live`
- Live transcript stream
- Recording status
- After stop: final transcript or summary
- After expiry: expired message

## 9. Open Decisions

The next session should ask or decide these items.

Hardware:

- Which Raspberry Pi model?
- Display size and resolution?
- Touchscreen or non-touch display?
- Physical toggle button required from the first prototype?
- Which microphone is available first?

Network:

- Local-only LAN access or internet-accessible link?
- Should QR clients connect directly to Raspberry Pi?
- Is there a separate server on Mac/GPU machine?
- Will `192.168.0.14` still be used as an Ollama/STT server?

AI:

- Real-time STT required in MVP, or batch STT after meeting is enough first?
- WhisperX required from the start?
- Is speaker diarization required in the first usable version?
- Which summary format is most important: Markdown, PPT, PDF, web page?

Data retention:

- Keep raw audio or delete after summary?
- Keep transcript locally?
- Need encryption at rest?
- Need admin-only recovery?

Product:

- Is `MeetKey` final product name?
- Should the device say `MeetKey` in English or `회의록 녹음기` in Korean?
- Is this for internal use, demo, or productization?

## 10. Suggested Prompt For Next Codex Session

If opening a new session from this project folder, start with something like:

```text
이 폴더는 MeetKey Recorder 프로젝트입니다. 000_INDEX.md를 먼저 읽고, 이전 대화에서 회수된 맥락을 기준으로 라즈베리파이 회의록 녹음기 MVP 설계를 이어가 주세요. 우선 장치 화면 프로세스와 소스 구조부터 잡고 싶습니다.
```

Good first next task:

```text
MeetKey의 MVP 1을 위해 로컬에서 실행되는 장치 화면 프로토타입을 만들어주세요. Idle, Recording, Complete, Expired 상태와 QR 생성/만료 흐름이 있어야 합니다.
```

## 11. Current Status

Created:

- Project folder
- Standard project buckets
- This index/context file

Not created yet:

- Source code
- Hardware bill of materials
- UI mockup
- STT pipeline
- Raspberry Pi deployment scripts

