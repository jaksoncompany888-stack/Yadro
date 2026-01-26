"""
Yadro v0 - Plan Manager

Builds execution plans from task specifications.
"""
from typing import Optional, Dict, List

from .models import Plan, Step, StepAction


class PlanManager:
    """
    Creates execution plans for tasks.

    MVP: Template-based plans for known task types.
    Scale: LLM-driven dynamic planning.
    """

    def build_plan(
        self,
        task_id: int,
        task_type: str,
        input_text: Optional[str] = None,
        input_data: Optional[Dict] = None,
    ) -> Plan:
        """
        Build execution plan for task.

        Args:
            task_id: Task ID
            task_type: Type of task
            input_text: User's text input
            input_data: Additional structured data

        Returns:
            Execution plan with steps
        """
        template_method = self._get_template(task_type)
        steps = template_method(input_text, input_data or {})

        return Plan.create(task_id=task_id, steps=steps)

    def _get_template(self, task_type: str):
        """Get plan template for task type."""
        templates = {
            "smm": self._template_smm,
            "smm_generate": self._template_smm_generate,
            "smm_edit": self._template_smm_edit,
            "smm_analyze": self._template_smm_analyze,
            "research": self._template_research,
            "summary": self._template_summary,
            "general": self._template_general,
        }
        return templates.get(task_type, self._template_general)

    # ==================== PLAN TEMPLATES ====================

    def _template_general(
        self,
        input_text: Optional[str],
        input_data: Dict,
    ) -> List[Step]:
        """General task template."""
        return [
            Step.create(
                action=StepAction.LLM_CALL,
                action_data={
                    "purpose": "analyze",
                    "input_text": input_text,
                },
            ),
            Step.create(
                action=StepAction.LLM_CALL,
                action_data={
                    "purpose": "execute",
                    "input_text": input_text,
                },
            ),
        ]

    def _template_smm(
        self,
        input_text: Optional[str],
        input_data: Dict,
    ) -> List[Step]:
        """SMM task template (legacy)."""
        return self._template_smm_generate(input_text, input_data)

    def _template_research(
        self,
        input_text: Optional[str],
        input_data: Dict,
    ) -> List[Step]:
        """Research task template."""
        search_step = Step.create(
            action=StepAction.TOOL_CALL,
            action_data={
                "tool": "web_search",
                "query": input_text,
            },
        )

        analyze_step = Step.create(
            action=StepAction.LLM_CALL,
            action_data={
                "purpose": "analyze_sources",
                "search_step_id": search_step.step_id,
            },
            depends_on=[search_step.step_id],
        )

        synthesize_step = Step.create(
            action=StepAction.LLM_CALL,
            action_data={
                "purpose": "synthesize",
                "analysis_step_id": analyze_step.step_id,
            },
            depends_on=[analyze_step.step_id],
        )

        return [search_step, analyze_step, synthesize_step]

    def _template_summary(
        self,
        input_text: Optional[str],
        input_data: Dict,
    ) -> List[Step]:
        """Summary task template."""
        steps = []

        url = input_data.get("url")
        if url:
            fetch_step = Step.create(
                action=StepAction.TOOL_CALL,
                action_data={
                    "tool": "web_fetch",
                    "url": url,
                },
            )
            steps.append(fetch_step)

            summary_step = Step.create(
                action=StepAction.LLM_CALL,
                action_data={
                    "purpose": "summarize",
                    "content_step_id": fetch_step.step_id,
                },
                depends_on=[fetch_step.step_id],
            )
            steps.append(summary_step)
        else:
            summary_step = Step.create(
                action=StepAction.LLM_CALL,
                action_data={
                    "purpose": "summarize",
                    "input_text": input_text,
                },
            )
            steps.append(summary_step)

        return steps

    def _template_smm_generate(
        self,
        input_text: Optional[str],
        input_data: Dict,
    ) -> List[Step]:
        """
        SMM Generate — генерация поста через архитектуру.

        Steps:
        1. memory_search — поиск похожих постов
        2. web_search — актуальная инфа (если нужно)
        3. generate_draft — генерация поста
        4. approval — ждём клиента
        """
        user_id = input_data.get("user_id", 0)
        topic = input_text or input_data.get("topic", "")
        smm_context = input_data.get("smm_context", "")
        skip_web_search = input_data.get("skip_web_search", False)

        steps = []

        # 1. Поиск похожих в памяти
        memory_step = Step.create(
            action=StepAction.TOOL_CALL,
            action_data={
                "tool": "memory_search",
                "user_id": user_id,
                "query": topic,
                "limit": 5,
            },
        )
        steps.append(memory_step)

        # 2. Поиск в интернете (опционально)
        if not skip_web_search:
            web_step = Step.create(
                action=StepAction.TOOL_CALL,
                action_data={
                    "tool": "web_search",
                    "query": topic,
                    "limit": 5,
                },
            )
            steps.append(web_step)
            depends_on = [memory_step.step_id, web_step.step_id]
        else:
            depends_on = [memory_step.step_id]

        # 3. Генерация поста
        generate_step = Step.create(
            action=StepAction.LLM_CALL,
            action_data={
                "purpose": "smm_generate_post",
                "input_text": topic,
                "smm_context": smm_context,
                "user_id": user_id,
            },
            depends_on=depends_on,
        )
        steps.append(generate_step)

        # 4. Approval — пауза для пользователя
        approval_step = Step.create(
            action=StepAction.APPROVAL,
            action_data={
                "message": "Проверьте черновик поста",
                "draft_step_id": generate_step.step_id,
            },
            depends_on=[generate_step.step_id],
        )
        steps.append(approval_step)

        return steps

    def _template_smm_edit(
        self,
        input_text: Optional[str],
        input_data: Dict,
    ) -> List[Step]:
        """
        SMM Edit — ТОЧЕЧНОЕ редактирование через архитектуру.

        Принцип: LLM генерит ТОЛЬКО новый контент, КОД применяет изменения.
        LLM НЕ видит весь пост — только задание на генерацию.

        Steps:
        1. parse_edit_intent — КОД парсит что хочет пользователь
        2. memory_search — контекст стиля (для генерации нового контента)
        3. web_search — инфо для контента (если нужно)
        4. generate_content — LLM генерит ТОЛЬКО новый контент (хук, абзац)
        5. apply_edits — КОД применяет изменения точечно
        """
        user_id = input_data.get("user_id", 0)
        original_text = input_data.get("original_text", "")
        edit_request = input_text or input_data.get("edit_request", "")
        topic = input_data.get("topic", "")  # тема поста для контекста

        steps = []

        # 1. Парсинг интента — КОД определяет операции
        parse_step = Step.create(
            action=StepAction.TOOL_CALL,
            action_data={
                "tool": "parse_edit_intent",
                "edit_request": edit_request,
                "original_text": original_text,
            },
        )
        steps.append(parse_step)

        # 2. Поиск контекста стиля в памяти
        memory_step = Step.create(
            action=StepAction.TOOL_CALL,
            action_data={
                "tool": "memory_search",
                "user_id": user_id,
                "query": f"стиль {topic}",
                "limit": 3,
            },
            depends_on=[parse_step.step_id],
        )
        steps.append(memory_step)

        # 3. Web search для контента (если генерируем новое)
        web_step = Step.create(
            action=StepAction.TOOL_CALL,
            action_data={
                "tool": "web_search",
                "query": topic,
                "limit": 3,
            },
            depends_on=[parse_step.step_id],
        )
        steps.append(web_step)

        # 4. Генерация ТОЛЬКО нового контента (хук, абзац)
        # LLM не видит весь пост — только задание
        generate_step = Step.create(
            action=StepAction.LLM_CALL,
            action_data={
                "purpose": "smm_generate_edit_content",
                "user_id": user_id,
                "topic": topic,
                # original_text НЕ передаём — LLM генерит только новое
            },
            depends_on=[parse_step.step_id, memory_step.step_id, web_step.step_id],
        )
        steps.append(generate_step)

        # 5. Применение изменений — КОД, не LLM
        apply_step = Step.create(
            action=StepAction.TOOL_CALL,
            action_data={
                "tool": "apply_edit_operations",
                "original_text": original_text,
                "user_id": user_id,
            },
            depends_on=[parse_step.step_id, generate_step.step_id],
        )
        steps.append(apply_step)

        return steps

    def _template_smm_analyze(
        self,
        input_text: Optional[str],
        input_data: Dict,
    ) -> List[Step]:
        """
        SMM Analyze — ГЛУБОКИЙ анализ канала через архитектуру.

        Steps:
        1. parse_channel — парсинг постов (топовые)
        2. compute_metrics — вычисление метрик БЕЗ LLM (код)
        3. deep_analyze — глубокий анализ с метриками (LLM)
        4. memory_store — сохранение результата

        Принцип: максимум логики в коде, минимум в промпте.
        """
        channel = input_text or input_data.get("channel", "")
        user_id = input_data.get("user_id", 0)

        # 1. Парсинг канала — топовые посты (20 — лимит веб-версии t.me/s/)
        parse_step = Step.create(
            action=StepAction.TOOL_CALL,
            action_data={
                "tool": "parse_channel",
                "channel": channel,
                "limit": 20,
                "top": True,
            },
        )

        # 2. Вычисление метрик БЕЗ LLM
        metrics_step = Step.create(
            action=StepAction.TOOL_CALL,
            action_data={
                "tool": "compute_channel_metrics",
                "source_step_id": parse_step.step_id,  # берём posts из parse
            },
            depends_on=[parse_step.step_id],
        )

        # 3. Глубокий анализ — LLM получает готовые метрики
        analyze_step = Step.create(
            action=StepAction.LLM_CALL,
            action_data={
                "purpose": "smm_deep_analyze",
                "input_text": channel,
                "user_id": user_id,
            },
            depends_on=[parse_step.step_id, metrics_step.step_id],
        )

        # 4. Сохранение в память
        store_step = Step.create(
            action=StepAction.TOOL_CALL,
            action_data={
                "tool": "memory_store",
                "user_id": user_id,
                "content": "",  # Будет заполнен из analyze_step
                "memory_type": "context",
                "importance": 0.85,
                "source_step_id": analyze_step.step_id,
            },
            depends_on=[analyze_step.step_id],
        )

        return [parse_step, metrics_step, analyze_step, store_step]
