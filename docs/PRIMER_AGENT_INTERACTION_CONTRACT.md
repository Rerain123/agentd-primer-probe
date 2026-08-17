# Primer Agent Interaction Contract v1

## Purpose

`primer_agent_interaction` is the structured, persistable UI payload emitted when the primer agent cannot safely continue without a user choice or data correction. It is distinct from a scientific result and from a free-text chat reply.

This contract is retained for upload compatibility, historical task recovery,
and internal REST clients. The primary MCP path for directly pasted sequences
uses `NO_POPUPS`: invalid template input returns a normal tool error, while
candidate shortfall and incomplete specificity continue to the final report.

Compatibility-mode WF-01 tasks may emit these objects for:

1. M1 preflight `BLOCKER` and `CONFIRMATION` issues;
2. recoverable M2, M3 and M4 execution errors.

M5 candidate shortfall is non-blocking. One or two candidates are delivered with
an `INSUFFICIENT_CANDIDATES` notice. Zero candidates still proceeds to M6 and
produces an empty-result report with `NO_DELIVERABLE_CANDIDATES` and available
stage-level reason codes.

## Payload ownership

| Owner | Permitted fields |
|---|---|
| Agent D | `blocker`, `message`, `context`, `options`, `inputFields` |
| Host backend | `taskId`, persistence metadata, optimistic-lock revision and audit fields |
| Frontend | `data.status`, `data.userResponse`, and values of fields explicitly exposed in `inputFields` |

The frontend must not modify the blocker code, options, scientific counts or technical context. The host backend must validate a change before persisting it.

## Lifecycle

1. Agent D returns a payload with `data.status=WAITING_USER`.
   Its frontend `ui_message` also carries
   `flowControl={action: WAIT_FOR_USER, stopCurrentTurn: true,
   resumeMessageType: agent_interaction_response}`. This is a hard assistant-turn
   boundary. At the MCP boundary, `structuredContent` is the authoritative host
   object and `content[0].text` contains the exact same object as compact JSON.
   The model must copy that text unchanged as the only visible assistant message.
   It must emit a bare JSON object without a Markdown
   fence, prefix, suffix, explanation, field rewrite, or natural-language option
   list. No later tool call is allowed in the same turn.
2. The host backend stores the complete JSON object and returns it to the frontend.
3. The frontend parses the exact assistant `agent_interaction` JSON and renders
   `message`, `options` and `inputFields`; ordinary chat text is not a valid popup payload.
4. On confirmation, the frontend emits one complete `agent_interaction_response` containing every currently waiting `interactionId`.
5. The host backend or MCP orchestrator submits that object to Agent D through `POST /v1/wf01/tasks/{batch_id}/interactions/response` or `submit_wf01_interaction_response`.
6. Agent D validates the selected actions, persists `USER_SUBMITTED`, routes the action, and marks the interaction `RESOLVED` atomically. When every M1 confirmation is resolved, the affected targets and batch advance to `QUEUED`.
7. The persisted routing result exposes `next_action`: `RUN_M2`, `RUN_M6`, or
   `WAIT_FOR_USER`. In the normal MCP path,
   `submit_wf01_interaction_response` automatically resumes deterministic
   execution for `RUN_M2`/`RUN_M6` and returns the next compact
   `WAIT_FOR_USER` or `COMPLETE` control summary. HiMat routes from structured
   fields and the exact JSON mirror, not from assistant prose.

The backend rejects every later workflow stage (M2-M6), not only M6 report
generation, while any interaction for the same task remains `WAITING_USER`,
`USER_SUBMITTED`, or `RUNNING`.

## Action routing

| Selected option | Host backend/orchestrator action |
|---|---|
| `RETRY_STAGE` | Retry only the failed stage after checking tool availability. |
| `CONTINUE_WITH_NOT_EVALUATED` | Accept the M1 unsupported-specificity scope, preserve an audit override, resolve the interaction and queue the batch when no other blocker remains. |
| `OTHER` | Send `userResponse.customInstruction` back to the agent as an explicit user instruction. |

## API exposure

The Agent D batch object includes `agent_interactions`. The host backend may read them from the normal batch response, or call `GET /v1/wf01/tasks/{batch_id}/interactions`.

The response write boundary is `POST /v1/wf01/tasks/{batch_id}/interactions/response`; the equivalent MCP tool is `submit_wf01_interaction_response`. The deterministic router accepts supported M1 confirmations and recoverable-stage responses. Candidate shortfall does not enter this interaction path. Older persisted M5 interactions remain readable for compatibility.

## MCP 对话弹窗映射

持久化对象 `primer_agent_interaction` 是服务端状态模型；Agent D 在结果的 `ui_message` 中生成
`agent_interaction` 弹窗模型。单题使用扁平结构；同时存在两题或更多题时按顺序
映射为 `interactions` 数组。详见
[AGENT_INTERACTION_POPUP_CONTRACT.md](AGENT_INTERACTION_POPUP_CONTRACT.md)。

当所有阻塞项均已解决、M6 成功生成报告后，智能体改为输出
[`primer_design_output`](PRIMER_DESIGN_OUTPUT_CONTRACT.md)，不再输出弹窗消息。
