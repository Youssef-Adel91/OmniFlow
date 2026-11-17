"""ai_workers/__init__.py — AI Processing Workers (Kafka Consumers)

Workers in this package:
  - semantic_router:    Routes events to appropriate LLM tier (L0/L1/L2/L3)
  - rag_engine:         Hybrid RAG retrieval + reranking
  - llm_invoker:        LLM API calls with retry, circuit breaker, cost tracking
  - outbound_dispatcher: Sends processed replies back through original channel
"""
