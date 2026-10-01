---
name: 🐛 Bug Report
about: 시스템 오류나 예기치 않은 동작을 발견했을 때 사용해주세요
title: "[BUG] "
labels: bug
assignees: ""
---

## 🚨 버그 요약 (Summary)
<!-- 발생한 버그를 1~2줄로 명확하게 설명해주세요. -->
- (예) 게스트 모드에서 PVT 결과 동기화 API 호출 시 500 에러가 발생함

## 👣 재현 방법 (Steps to Reproduce)
<!-- 버그를 재현하기 위한 구체적인 순서를 적어주세요. -->
1. 게스트 모드로 앱 진입
2. PVT 테스트 완료 후 결과 화면 진입
3.

## 🎯 예상 동작 vs 실제 동작 (Expected vs Actual)
- **예상 동작:** Brain ROI 점수와 추천 작업이 정상적으로 반환되어야 함
- **실제 동작:** 무한 로딩이 돌며 `ProblemDetail` 형태로 500 에러(S001) 반환됨

## 📱 환경 정보 (Environment)
<!-- 문제 해결을 위한 컨텍스트를 제공해주세요. 모르는 부분은 비워두셔도 됩니다. -->
- **사용자 상태:** (애플 로그인 유저 / 게스트 유저)
- **발생 화면/API:** (예: 홈 화면 / `POST /api/v1/evaluations`)
- **기기/OS:** (예: iPhone 13 / iOS 17)

## 🔍 단서 및 로그 (Logs & Payload)
<!-- iOS에서 보낸 Request JSON이나 백엔드에서 받은 Response 에러 코드(ErrorCode), 스크린샷 등이 있다면 반드시 첨부해주세요. -->
<details>
<summary>에러 로그 또는 페이로드 보기</summary>

```json
// 여기에 Request/Response JSON 또는 에러 로그를 붙여넣어 주세요.