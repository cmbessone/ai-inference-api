workspace "AI Inference API" "Modelo C4 de la API de inferencia escalable y de alta disponibilidad" {

    model {
        cliente = person "Cliente de la plataforma" "Backend o app de un cliente SaaS"
        sre = person "SRE / Operaciones" "Monitorea SLOs y despliega"

        llm = softwareSystem "Proveedor LLM" "OpenAI u otro; mock por defecto" "Externo"
        observabilidad = softwareSystem "Observabilidad" "Prometheus + logs" "Externo"

        inferenceApi = softwareSystem "AI Inference API" "Inferencia LLM sync/SSE y clasificación propia" {
            lb = container "Load Balancer" "least_conn, failover, SSE sin buffering" "nginx"
            api = container "API (xN réplicas)" "Stateless: auth, rate limit, cache, breaker, SSE, ML" "Python 3.12 · FastAPI · uvicorn" {
                middlewares = component "Middlewares ASGI" "request_id, log de acceso, métricas, load shedding"
                dependencias = component "Dependencias de router" "auth (X-API-Key) + rate limit"
                rutas = component "Rutas" "infer, stream, classify, health, metrics"
                inferencia = component "InferenceService" "cache-aside + single-flight + lock distribuido"
                streaming = component "StreamingService" "productor/consumidor acotado + cancelación"
                clasificacion = component "ClassificationService" "registry + micro-batcher + thread pool"
                clienteLlm = component "ResilientLLMClient" "Decorator: breaker, bulkhead, timeout, retry"
                breaker = component "CircuitBreaker" "núcleo puro + stores Redis/memoria"
                rateLimiter = component "RateLimiter" "sliding log Lua + fallback local"
                gateway = component "RedisGateway" "timeout + fail fast"
                registry = component "ModelRegistry" "v1/v2 verificados por SHA-256"
                auditoria = component "QueuedAuditSink" "cola acotada → Redis Streams"
            }
            redis = container "Redis" "Cuotas, cache, locks, breaker, audit stream" "Redis 7" "Database"
        }

        cliente -> lb "Usa" "HTTPS + X-API-Key"
        lb -> api "Balancea" "HTTP/1.1 keep-alive"
        api -> redis "Estado compartido" "RESP · Lua"
        api -> llm "Completions / streaming" "HTTPS"
        observabilidad -> api "Scrapea /metrics"
        sre -> observabilidad "Consulta"

        middlewares -> dependencias
        dependencias -> rateLimiter
        dependencias -> rutas
        rutas -> inferencia
        rutas -> streaming
        rutas -> clasificacion
        inferencia -> clienteLlm
        streaming -> clienteLlm
        streaming -> auditoria
        clienteLlm -> breaker
        clasificacion -> registry
        rateLimiter -> gateway
        breaker -> gateway
        auditoria -> gateway
        gateway -> redis
        clienteLlm -> llm
    }

    views {
        systemContext inferenceApi "Contexto" {
            include *
            autolayout lr
        }
        container inferenceApi "Contenedores" {
            include *
            autolayout lr
        }
        component api "Componentes" {
            include *
            autolayout tb
        }
        styles {
            element "Person" {
                shape Person
                background #0f766e
                color #ffffff
            }
            element "Externo" {
                background #6b7280
                color #ffffff
            }
            element "Database" {
                shape Cylinder
            }
        }
    }
}
