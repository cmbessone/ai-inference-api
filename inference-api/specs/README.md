# Specs (SDD)

| Spec | Tema |
|------|------|
| [000](000-constitution.md) | Constitución: principios y estándares |
| [001](001-infer/spec.md) | R1 — Inferencia sincrónica |
| [002](002-stream/spec.md) | R2 — Streaming SSE |
| [003](003-classify/spec.md) | R3 — Clasificación ML |
| [004](004-resilience/spec.md) | Resiliencia y alta disponibilidad |
| [005](005-observability/spec.md) | Observabilidad |
| [plan](plan.md) · [tasks](tasks.md) | Plan técnico y tareas |

## Trazabilidad (generada con `make trace`)

<!-- trace:start -->
| Criterio | Tests |
|---|---|
| R1-AC01 | `test_infer_api::test_missing_api_key_is_401` |
| R1-AC02 | `test_infer_api::test_invalid_key_is_401_and_consumes_no_quota` |
| R1-AC03 | `test_infer_api::test_success_contract` |
| R1-AC04 | `test_infer_api::test_rate_limit_61st_request_is_429`<br>`test_rate_limiter::test_request_61_is_rejected_with_exact_retry_after` |
| R1-AC05 | `test_rate_limiter::test_quota_is_shared_between_replicas` |
| R1-AC06 | `test_rate_limiter::test_window_is_sliding_not_fixed` |
| R1-AC07 | `test_infer_api::test_success_contract`<br>`test_rate_limiter::test_allows_until_limit_and_reports_remaining` |
| R1-AC08 | `test_ops_api::test_degrades_gracefully_without_redis`<br>`test_rate_limiter::test_falls_back_to_local_limiter_when_redis_down` |
| R1-AC09 | `test_infer_api::test_repeated_request_hits_cache`<br>`test_cache::test_same_request_same_key`<br>`test_inference_service::test_second_request_is_served_from_cache` |
| R1-AC10 | `test_cache::test_model_or_config_change_key`<br>`test_inference_service::test_different_config_is_not_coalesced` |
| R1-AC11 | `test_infer_api::test_cache_ttl_is_configurable`<br>`test_cache::test_cache_ttl_is_applied` |
| R1-AC12 | `test_ops_api::test_degrades_gracefully_without_redis`<br>`test_cache::test_cache_degrades_to_miss_when_redis_down` |
| R1-AC13 | `test_infer_api::test_success_contract` |
| R1-AC14 | `test_infer_api::test_unknown_model_is_400`<br>`test_infer_api::test_invalid_body_is_422` |
| R1-AC15 | `test_infer_api::test_open_circuit_is_503_without_calling_provider`<br>`test_llm_client::test_circuit_opens_after_five_failures_and_rejects_fast` |
| R1-AC16 | `test_infer_api::test_timeout_is_504`<br>`test_llm_client::test_timeout_raises_and_counts_as_failure` |
| R1-AC17 | `test_inference_service::test_100_identical_requests_one_llm_call_in_one_replica`<br>`test_inference_service::test_identical_requests_across_replicas_one_llm_call` |
| R2-AC01 | `test_stream_api::test_stream_contract`<br>`test_streaming::test_sse_format_matches_contract`<br>`test_streaming::test_tokens_then_done` |
| R2-AC02 | `test_streaming::test_open_circuit_emits_single_error_without_calling_provider` |
| R2-AC03 | `test_stream_api::test_client_disconnect_cancels_upstream_end_to_end`<br>`test_streaming::test_client_disconnect_cancels_upstream_and_audits` |
| R2-AC04 | `test_streaming::test_timeout_emits_error_and_cancels_upstream` |
| R2-AC05 | `test_stream_api::test_stream_is_audited_in_redis`<br>`test_streaming::test_client_disconnect_cancels_upstream_and_audits`<br>`test_streaming::test_audit_sink_never_blocks_and_counts_drops` |
| R2-AC06 | `test_stream_api::test_stream_contract` |
| R2-AC07 | `test_streaming::test_backpressure_bounds_buffer_and_aborts_slow_consumer` |
| R2-AC08 | `test_streaming::test_backpressure_bounds_buffer_and_aborts_slow_consumer` |
| R3-AC01 | `test_ml_model::test_registry_loads_both_versions` |
| R3-AC02 | `test_ml_model::test_loading_does_not_block_event_loop` |
| R3-AC03 | `test_classify_api::test_classify_with_version_header`<br>`test_ml_model::test_resolve_version_uses_header_or_default` |
| R3-AC04 | `test_classify_api::test_default_version_is_v2`<br>`test_ml_model::test_resolve_version_uses_header_or_default` |
| R3-AC05 | `test_classify_api::test_unknown_version_is_400`<br>`test_ml_model::test_unknown_version_lists_available` |
| R3-AC06 | `test_ml_model::test_inference_runs_in_worker_thread` |
| R3-AC07 | `test_classify_api::test_classify_with_version_header` |
| R3-AC08 | `test_ml_model::test_tampered_artifact_is_rejected` |
| R3-AC09 | `test_ml_model::test_concurrent_requests_are_batched_and_routed_back` |
| RS-AC01 | `test_circuit_breaker::test_opens_after_five_failures_within_window`<br>`test_circuit_breaker::test_failures_outside_window_do_not_open`<br>`test_llm_client::test_circuit_opens_after_five_failures_and_rejects_fast` |
| RS-AC02 | `test_circuit_breaker::test_open_rejects_with_remaining_time`<br>`test_llm_client::test_circuit_opens_after_five_failures_and_rejects_fast` |
| RS-AC03 | `test_circuit_breaker::test_half_open_allows_single_probe`<br>`test_circuit_breaker::test_redis_store_single_probe_across_cluster` |
| RS-AC04 | `test_circuit_breaker::test_probe_success_closes_and_failure_reopens`<br>`test_circuit_breaker::test_redis_store_single_probe_across_cluster`<br>`test_circuit_breaker::test_redis_store_probe_failure_reopens` |
| RS-AC05 | `test_llm_client::test_client_errors_do_not_open_circuit` |
| RS-AC06 | `test_circuit_breaker::test_redis_store_is_shared_between_replicas` |
| RS-AC07 | `test_ops_api::test_degrades_gracefully_without_redis`<br>`test_circuit_breaker::test_fallback_store_uses_memory_when_redis_down` |
| RS-AC08 | `test_ops_api::test_breaker_policy_is_configurable` |
| RS-AC09 | `test_llm_client::test_bulkhead_rejects_when_saturated` |
| RS-AC10 | `test_ops_api::test_load_shedding_rejects_excess` |
| RS-AC11 | `test_redis_gateway::test_cooldown_after_repeated_failures` |
| RS-AC12 | `test_llm_client::test_transient_error_is_retried` |
| RS-AC13 | `test_ops_api::test_readiness_fails_during_shutdown`<br>`test_streaming::test_audit_worker_drains_on_shutdown` |
| RS-AC14 | `test_stack::test_survives_replica_crash` |
| OB-AC01 | `test_logging::test_access_log_has_required_fields_and_no_secrets`<br>`test_request_context::test_classify_outcome` |
| OB-AC02 | `test_request_context::test_resolve_request_id_reuses_safe_incoming_id`<br>`test_request_context::test_resolve_request_id_generates_uuid_for_unsafe_values`<br>`test_request_context::test_response_echoes_request_id` |
| OB-AC03 | `test_logging::test_redact_sensitive_masks_secrets`<br>`test_logging::test_access_log_has_required_fields_and_no_secrets` |
| OB-AC04 | `test_ops_api::test_overall_status`<br>`test_ops_api::test_health_report` |
| OB-AC05 | `test_ops_api::test_readiness_and_liveness`<br>`test_request_context::test_liveness` |
| OB-AC06 | `test_ops_api::test_readiness_and_liveness`<br>`test_ops_api::test_readiness_fails_during_shutdown` |
| OB-AC07 | `test_ops_api::test_metrics_exposes_required_series`<br>`test_ops_api::test_metrics_can_require_a_token`<br>`test_request_context::test_metric_route_label_bounds_cardinality` |
| OB-AC08 | `test_stack::test_sustains_100_rps_under_sla` |
<!-- trace:end -->
