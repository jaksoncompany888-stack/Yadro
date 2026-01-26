import { useState, useEffect } from 'react'
import { format } from 'date-fns'
import { api } from '../api/client'

/**
 * Post Editor Component
 *
 * Create and edit posts with AI assistance.
 */
export default function PostEditor({ post, date, onSave, onCancel, initData }) {
  const [text, setText] = useState(post?.text || '')
  const [topic, setTopic] = useState(post?.topic || '')
  const [publishAt, setPublishAt] = useState(
    post?.publish_at ? new Date(post.publish_at) : date || null
  )
  const [publishTime, setPublishTime] = useState(
    post?.publish_at ? format(new Date(post.publish_at), 'HH:mm') : '10:00'
  )
  const [platforms, setPlatforms] = useState(post?.platforms || ['telegram'])
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [aiPrompt, setAiPrompt] = useState('')

  // Combine date and time
  const getPublishDateTime = () => {
    if (!publishAt) return null
    const [hours, minutes] = publishTime.split(':').map(Number)
    const dt = new Date(publishAt)
    dt.setHours(hours, minutes, 0, 0)
    return dt.toISOString()
  }

  const handleSave = async (asDraft = false) => {
    setLoading(true)
    try {
      await onSave({
        text,
        topic,
        platforms,
        channel_ids: {}, // Will be filled from user settings
        publish_at: asDraft ? null : getPublishDateTime(),
      })
    } finally {
      setLoading(false)
    }
  }

  const handleGenerate = async () => {
    if (!topic.trim()) {
      alert('Введите тему поста')
      return
    }

    setGenerating(true)
    try {
      const result = await api.generatePost(topic, {}, initData)
      setText(result.text)
    } catch (error) {
      console.error('Generation failed:', error)
      alert('Ошибка генерации: ' + error.message)
    } finally {
      setGenerating(false)
    }
  }

  const handleAiEdit = async () => {
    if (!aiPrompt.trim() || !text.trim()) return

    setGenerating(true)
    try {
      const result = await api.editPost(text, aiPrompt, initData)
      setText(result.text)
      setAiPrompt('')
    } catch (error) {
      console.error('Edit failed:', error)
      alert('Ошибка редактирования: ' + error.message)
    } finally {
      setGenerating(false)
    }
  }

  const togglePlatform = (platform) => {
    setPlatforms((prev) =>
      prev.includes(platform)
        ? prev.filter((p) => p !== platform)
        : [...prev, platform]
    )
  }

  return (
    <div className="space-y-4">
      {/* Topic input */}
      <div>
        <label className="block text-sm font-medium text-tg-text mb-1">
          Тема
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            value={topic}
            onChange={(e) => setTopic(e.target.value)}
            placeholder="О чём пост?"
            className="flex-1 px-4 py-3 rounded-xl bg-tg-secondary-bg text-tg-text placeholder-tg-hint border-0 focus:ring-2 focus:ring-tg-button"
          />
          <button
            onClick={handleGenerate}
            disabled={generating}
            className="px-4 py-3 bg-tg-button text-tg-button-text rounded-xl font-medium disabled:opacity-50"
          >
            {generating ? '...' : '✨ AI'}
          </button>
        </div>
      </div>

      {/* Text editor */}
      <div>
        <label className="block text-sm font-medium text-tg-text mb-1">
          Текст поста
        </label>
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Напишите пост или нажмите AI для генерации..."
          rows={8}
          className="w-full px-4 py-3 rounded-xl bg-tg-secondary-bg text-tg-text placeholder-tg-hint border-0 focus:ring-2 focus:ring-tg-button resize-none"
        />
        <div className="text-right text-xs text-tg-hint mt-1">
          {text.length} символов
        </div>
      </div>

      {/* AI edit */}
      {text && (
        <div className="flex gap-2">
          <input
            type="text"
            value={aiPrompt}
            onChange={(e) => setAiPrompt(e.target.value)}
            placeholder="Редактировать: добавь хук, сократи..."
            className="flex-1 px-4 py-2 rounded-lg bg-tg-secondary-bg text-tg-text text-sm placeholder-tg-hint border-0"
            onKeyDown={(e) => e.key === 'Enter' && handleAiEdit()}
          />
          <button
            onClick={handleAiEdit}
            disabled={generating || !aiPrompt.trim()}
            className="px-3 py-2 text-tg-link text-sm disabled:opacity-50"
          >
            Применить
          </button>
        </div>
      )}

      {/* Platforms */}
      <div>
        <label className="block text-sm font-medium text-tg-text mb-2">
          Платформы
        </label>
        <div className="flex gap-2">
          <button
            onClick={() => togglePlatform('telegram')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              platforms.includes('telegram')
                ? 'bg-tg-button text-tg-button-text'
                : 'bg-tg-secondary-bg text-tg-text'
            }`}
          >
            📱 Telegram
          </button>
          <button
            onClick={() => togglePlatform('vk')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              platforms.includes('vk')
                ? 'bg-tg-button text-tg-button-text'
                : 'bg-tg-secondary-bg text-tg-text'
            }`}
          >
            🔵 VK
          </button>
        </div>
      </div>

      {/* Schedule */}
      <div>
        <label className="block text-sm font-medium text-tg-text mb-2">
          Время публикации
        </label>
        <div className="flex gap-2">
          <input
            type="date"
            value={publishAt ? format(publishAt, 'yyyy-MM-dd') : ''}
            onChange={(e) => setPublishAt(e.target.value ? new Date(e.target.value) : null)}
            className="flex-1 px-4 py-2 rounded-lg bg-tg-secondary-bg text-tg-text border-0"
          />
          <input
            type="time"
            value={publishTime}
            onChange={(e) => setPublishTime(e.target.value)}
            className="px-4 py-2 rounded-lg bg-tg-secondary-bg text-tg-text border-0"
          />
        </div>
      </div>

      {/* Preview */}
      {text && (
        <div>
          <label className="block text-sm font-medium text-tg-text mb-2">
            Превью
          </label>
          <div className="bg-white rounded-xl p-4 border border-gray-200">
            <div
              className="prose prose-sm max-w-none"
              dangerouslySetInnerHTML={{ __html: text.replace(/\n/g, '<br>') }}
            />
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3 pt-4">
        <button
          onClick={onCancel}
          className="flex-1 py-3 rounded-xl bg-tg-secondary-bg text-tg-text font-medium"
        >
          Отмена
        </button>
        <button
          onClick={() => handleSave(true)}
          disabled={loading || !text.trim()}
          className="flex-1 py-3 rounded-xl bg-tg-secondary-bg text-tg-link font-medium disabled:opacity-50"
        >
          Черновик
        </button>
        <button
          onClick={() => handleSave(false)}
          disabled={loading || !text.trim() || !publishAt}
          className="flex-1 py-3 rounded-xl bg-tg-button text-tg-button-text font-medium disabled:opacity-50"
        >
          {loading ? '...' : 'Запланировать'}
        </button>
      </div>
    </div>
  )
}
