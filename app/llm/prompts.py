"""
Yadro v0 - Prompt Templates

System prompts and templates for different tasks.
"""
from typing import Optional, Dict, Any


# System prompts
SYSTEM_PROMPTS = {
    "default": """You are Yadro, a helpful AI assistant. 
You help users complete tasks efficiently and accurately.
Be concise and practical in your responses.""",

    "smm": """You are Yadro, an AI assistant specialized in social media marketing.
You help create engaging posts for social media channels.

STYLE GUIDELINES:
- Write like a real person, not a corporation
- Use conversational Russian language
- Emojis are OK but don't overdo (1-3 per post)
- Short paragraphs, easy to scan on mobile

AVOID AI-SOUNDING PATTERNS (CRITICAL):
- NO "знаменует новую эру", "является свидетельством", "служит примером"
- NO "не просто X, это Y" constructions
- NO "эксперты отмечают" without specific names
- NO empty phrases: "в современном мире", "в наше время"
- NO corporate buzzwords: "синергия", "экосистема", "парадигма"
- NO lists of exactly 3 items without reason
- NO repeating same idea with synonyms

GOOD PATTERNS:
- Direct address to reader (ты/вы)
- Specific numbers and facts
- Questions that spark engagement
- Personal opinion or experience
- Humor where appropriate""",

    "research": """You are Yadro, an AI research assistant.
You help users gather and synthesize information.
Guidelines:
- Be thorough and accurate
- Cite sources when available
- Present information in a structured way
- Highlight key findings""",

    "summary": """You are Yadro, an AI assistant specialized in summarization.
You help users understand content quickly.
Guidelines:
- Extract key points
- Be concise but complete
- Preserve important details
- Use clear structure""",

    "analysis": """You are Yadro, an AI analyst.
You help users understand and analyze information.
Guidelines:
- Be objective and thorough
- Consider multiple perspectives
- Support conclusions with evidence
- Identify patterns and insights""",
}


# Task-specific templates
TASK_TEMPLATES = {
    "analyze": """Analyze the following input and determine the best approach to complete the task.

Input: {input_text}

Provide a brief analysis of:
1. What the user wants
2. Key requirements
3. Suggested approach""",

    "execute": """Complete the following task based on the analysis.

Task: {input_text}

{context}

Provide a complete, high-quality response.""",

    "research": """Research the following topic and gather relevant information.

Topic: {input_text}

Focus on:
- Key facts and data
- Recent developments
- Multiple perspectives
- Reliable sources""",

    "generate_draft": """Create a social media post based on the following.

Topic: {input_text}
Channel: {channel}

Requirements:
- Engaging and appropriate for the platform
- Clear call to action if relevant
- Appropriate length for the channel""",

    "analyze_sources": """Analyze the following search results and extract key information.

Search Results:
{search_results}

Provide:
1. Key findings
2. Source quality assessment
3. Information gaps""",

    "synthesize": """Synthesize the following analysis into a comprehensive response.

Analysis:
{analysis}

Create a well-structured summary that:
- Addresses the main question
- Incorporates key findings
- Is clear and actionable""",

    "summarize": """Summarize the following content.

Content:
{content}

Provide:
1. Main points (bullet list)
2. Key takeaways
3. Brief overall summary""",
}


class PromptBuilder:
    """
    Builds prompts from templates.
    """
    
    def __init__(
        self,
        system_prompts: Optional[Dict[str, str]] = None,
        task_templates: Optional[Dict[str, str]] = None,
    ):
        """
        Initialize PromptBuilder.
        
        Args:
            system_prompts: Custom system prompts
            task_templates: Custom task templates
        """
        self._system_prompts = system_prompts or SYSTEM_PROMPTS
        self._task_templates = task_templates or TASK_TEMPLATES
    
    def get_system_prompt(self, task_type: str = "default") -> str:
        """
        Get system prompt for task type.
        
        Args:
            task_type: Type of task
            
        Returns:
            System prompt string
        """
        return self._system_prompts.get(task_type, self._system_prompts["default"])
    
    def build_prompt(
        self,
        template_name: str,
        **kwargs,
    ) -> str:
        """
        Build prompt from template.
        
        Args:
            template_name: Name of template
            **kwargs: Template variables
            
        Returns:
            Formatted prompt
        """
        template = self._task_templates.get(template_name)
        if template is None:
            # Return raw input if no template
            return kwargs.get("input_text", "")
        
        # Fill in template variables
        try:
            return template.format(**kwargs)
        except KeyError as e:
            # Return template with missing vars as placeholders
            return template
    
    def add_system_prompt(self, name: str, prompt: str) -> None:
        """Add custom system prompt."""
        self._system_prompts[name] = prompt
    
    def add_task_template(self, name: str, template: str) -> None:
        """Add custom task template."""
        self._task_templates[name] = template


# Global instance
prompt_builder = PromptBuilder()
