# MeetKey Recorder

![라즈베리파이 터치스크린에서 실행 중인 MeetKey Recorder](assets/meetkey-device.jpg)

MeetKey Recorder는 라즈베리파이, 5인치 터치스크린, USB 컨퍼런스 마이크를 이용해 만든 **회의 녹음/전사/요약 PoC**입니다. 회의실에 놓고 버튼 하나로 녹음을 시작한 뒤, 저장하면 휴대폰에서 QR로 접속해 원본 음성, 전사문, 요약문을 확인하고 다운로드할 수 있는 흐름을 목표로 합니다.

이 저장소에는 라즈베리파이에서 동작하는 녹음 장치 UI, 휴대폰 확인용 로컬 웹페이지, Whisper 전사와 Gemma/Ollama 요약을 수행하는 처리 서버 코드가 함께 들어 있습니다.

## 핵심 기능

- 라즈베리파이 터치스크린에서 전체 화면 키오스크 UI로 실행
- Anker PowerConf USB 컨퍼런스 마이크를 이용한 WAV 녹음
- 녹음 시작, 일시정지, 재개, 저장, 취소 흐름 지원
- 녹음 중 마이크 입력 레벨 시각화
- 원본 음성 파일을 라즈베리파이에 로컬 저장
- 긴 회의를 10분 단위 구간으로 나누고 일부 오버랩을 둔 뒤 순차 처리
- 임시 오디오 구간만 처리 서버로 전송해 Whisper 전사와 Gemma 요약 수행
- 휴대폰에서 QR로 접속하는 회의 기록/현재 회의 페이지 제공
- Markdown 기반 전사문/요약문 렌더링
- 원본 음성, 전사문, 요약문 다운로드 지원
- 처리 서버는 AI 작업자 역할만 수행하고 최종 기록은 라즈베리파이가 소유

## 사용 장비

- Raspberry Pi 4
- 5인치 터치스크린
- Anker PowerConf A3301 USB 컨퍼런스 마이크
- USB Wi-Fi 동글
- 라즈베리파이 내장 Wi-Fi 핫스팟

## 시스템 구조

```text
라즈베리파이 터치스크린
  -> 녹음 장치 UI 실행
  -> 원본 WAV와 최종 회의 기록 저장
  -> 휴대폰용 웹페이지와 QR 제공

휴대폰
  -> MeetKey 핫스팟 접속
  -> QR로 기록/현재 회의 페이지 열기
  -> 전사문, 요약문, 원본 음성 확인 및 다운로드

처리 서버
  -> 임시 오디오 구간 수신
  -> Whisper STT 실행
  -> Gemma/Ollama 요약 실행
  -> 전사/요약 결과를 JSON으로 반환
```

## 폴더 구조

```text
02_Source/meetkey_poc/
  라즈베리파이 녹음 앱, 키오스크 UI, 휴대폰 페이지, systemd 설정, 핫스팟 스크립트

02_Source/meetkey_server/
  처리 서버, STT 래퍼, 기록/세션 페이지, 다운로드/저장/삭제 API

06_Docs/
  제품 기획 메모, PoC 명세, 요약 프롬프트 참고 문서

assets/
  README 이미지
```

## 라즈베리파이 앱 실행

라즈베리파이 앱은 Python HTTP 서버와 정적 HTML/CSS/JS 화면으로 구성되어 있습니다.

```bash
cd 02_Source/meetkey_poc
cp config.example.json config.json
python3 app.py
```

장치 화면:

```text
http://localhost:8000/device
```

실제 배포 장치에서는 systemd와 Chromium 키오스크 모드를 사용합니다.

```bash
./scripts/start_server.sh
./scripts/open_kiosk.sh
```

## 처리 서버 실행

처리 서버는 라즈베리파이에서 보낸 오디오 구간을 받아 전사와 요약 결과를 반환합니다.

```bash
cd 02_Source/meetkey_server
cp config.example.json config.json
python3 app.py
```

실제 전사를 위해서는 아래 중 하나를 설정할 수 있습니다.

- 외부 전사 명령어
- WhisperX
- faster-whisper
- 로컬 ASR API 래퍼

가벼운 통합 테스트는 mock 모드로 실행할 수 있습니다.

```bash
MEETKEY_STT_MODE=mock MEETKEY_SUMMARY_MODE=mock python3 app.py
```

## 설정 관리

실제 런타임 설정 파일은 git에 올리지 않습니다.

아래 예시 파일을 복사해 각 환경에 맞게 수정합니다.

```text
02_Source/meetkey_poc/config.example.json
02_Source/meetkey_server/config.example.json
```

실제 Wi-Fi 비밀번호, 서버 주소, 녹음 파일, 전사/요약 결과, 로컬 배포 설정은 커밋하지 않는 것을 원칙으로 합니다.

## 현재 구현 상태

현재 end-to-end로 동작하는 항목:

- 라즈베리파이 녹음 플로우
- 원본 WAV 로컬 저장
- 일시정지, 재개, 취소, 저장
- 휴대폰 QR 접속
- 녹음 기록 목록과 선택한 기록 QR 표시
- Markdown 기반 전사/요약 페이지
- 10분 단위 구간 전사/요약 처리
- 원본 음성/전사문/요약문 다운로드
- 처리 서버 연동

앞으로 개선할 항목:

- 실패한 구간 처리 재시도 큐
- 저장/삭제 등 기록 생명주기 UX 강화
- GPIO 하드웨어 버튼 연동
- 화자 분리와 단어 단위 타임스탬프 개선
- 새 라즈베리파이 설치 자동화
- 장시간 회의 테스트와 안정성 보강

## 개발 의도

이 프로젝트는 완성된 제품이 아니라, 실제 회의실에서 사용할 수 있는 녹음 장치 UX를 검증하기 위한 PoC입니다. 핵심 목표는 “회의가 끝난 직후 휴대폰으로 바로 결과를 확인할 수 있는가”, “긴 회의도 빠르게 처리되는 것처럼 느껴지는가”, “원본 데이터 소유권을 라즈베리파이에 둘 수 있는가”를 확인하는 것입니다.
