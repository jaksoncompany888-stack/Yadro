import { useState, useEffect, useRef } from 'react'
import { format, addDays, startOfWeek, isSameDay, isToday } from 'date-fns'
import { ru } from 'date-fns/locale'
import { api } from '../api/client'

/**
 * Week Calendar Component
 *
 * Shows 7 days with posts. Supports drag & drop.
 */
export default function Calendar({ onDateSelect, onPostSelect, initData }) {
  const [weekOffset, setWeekOffset] = useState(0)
  const [calendarData, setCalendarData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [draggedPost, setDraggedPost] = useState(null)
  const [dropTarget, setDropTarget] = useState(null)

  useEffect(() => {
    loadCalendar()
  }, [weekOffset, initData])

  const loadCalendar = async () => {
    try {
      setLoading(true)
      const data = await api.getWeek(weekOffset, initData)
      setCalendarData(data)
    } catch (error) {
      console.error('Failed to load calendar:', error)
    } finally {
      setLoading(false)
    }
  }

  const goToPrevWeek = () => setWeekOffset(w => w - 1)
  const goToNextWeek = () => setWeekOffset(w => w + 1)
  const goToToday = () => setWeekOffset(0)

  // Drag & Drop handlers
  const handleDragStart = (post) => {
    setDraggedPost(post)
  }

  const handleDragEnd = () => {
    setDraggedPost(null)
    setDropTarget(null)
  }

  const handleDragOver = (date) => {
    setDropTarget(date)
  }

  const handleDrop = async (targetDate) => {
    if (!draggedPost) return

    // Don't move if same date
    const currentDate = draggedPost.publish_at
      ? new Date(draggedPost.publish_at).toDateString()
      : null
    if (currentDate === targetDate.toDateString()) {
      handleDragEnd()
      return
    }

    try {
      // Keep the same time, just change the date
      const currentTime = draggedPost.publish_at
        ? format(new Date(draggedPost.publish_at), 'HH:mm')
        : '10:00'
      const [hours, minutes] = currentTime.split(':').map(Number)
      const newDate = new Date(targetDate)
      newDate.setHours(hours, minutes, 0, 0)

      await api.updatePost(draggedPost.id, {
        publish_at: newDate.toISOString(),
        status: 'scheduled',
      }, initData)

      // Reload calendar
      await loadCalendar()
    } catch (error) {
      console.error('Failed to move post:', error)
    }

    handleDragEnd()
  }

  if (loading && !calendarData) {
    return (
      <div className="flex items-center justify-center py-12">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-tg-button"></div>
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {/* Navigation */}
      <div className="flex items-center justify-between">
        <button
          onClick={goToPrevWeek}
          className="p-2 text-tg-link hover:bg-tg-secondary-bg rounded-lg"
        >
          ← Назад
        </button>

        <button
          onClick={goToToday}
          className="px-4 py-2 text-sm text-tg-link hover:bg-tg-secondary-bg rounded-lg"
        >
          Сегодня
        </button>

        <button
          onClick={goToNextWeek}
          className="p-2 text-tg-link hover:bg-tg-secondary-bg rounded-lg"
        >
          Вперёд →
        </button>
      </div>

      {/* Stats */}
      {calendarData && (
        <div className="flex justify-center gap-4 text-sm text-tg-hint">
          <span>Всего: {calendarData.total_posts}</span>
          <span>Запланировано: {calendarData.total_scheduled}</span>
          <span>Опубликовано: {calendarData.total_published}</span>
        </div>
      )}

      {/* Days */}
      <div className="space-y-3">
        {calendarData?.days.map((day) => (
          <DayCard
            key={day.date}
            day={day}
            onDateSelect={onDateSelect}
            onPostSelect={onPostSelect}
            onDragStart={handleDragStart}
            onDragEnd={handleDragEnd}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            isDragOver={dropTarget && format(dropTarget, 'yyyy-MM-dd') === day.date}
            draggedPost={draggedPost}
          />
        ))}
      </div>
    </div>
  )
}

function DayCard({
  day,
  onDateSelect,
  onPostSelect,
  onDragStart,
  onDragEnd,
  onDragOver,
  onDrop,
  isDragOver,
  draggedPost,
}) {
  const date = new Date(day.date)
  const today = isToday(date)

  const handleDragOver = (e) => {
    e.preventDefault()
    onDragOver(date)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    onDrop(date)
  }

  return (
    <div
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      className={`rounded-xl p-4 transition-all ${
        isDragOver
          ? 'bg-tg-button/20 border-2 border-tg-button border-dashed'
          : today
            ? 'bg-tg-button/10 border-2 border-tg-button'
            : 'bg-tg-secondary-bg'
      }`}
    >
      {/* Day header */}
      <div className="flex items-center justify-between mb-3">
        <div>
          <div className={`font-medium ${today ? 'text-tg-button' : 'text-tg-text'}`}>
            {format(date, 'EEEE', { locale: ru })}
            {today && <span className="ml-2 text-xs">(сегодня)</span>}
          </div>
          <div className="text-sm text-tg-hint">
            {format(date, 'd MMMM', { locale: ru })}
          </div>
        </div>

        <button
          onClick={() => onDateSelect(date)}
          className="p-2 text-tg-link hover:bg-white/50 rounded-lg text-xl"
        >
          +
        </button>
      </div>

      {/* Drop hint */}
      {isDragOver && draggedPost && (
        <div className="text-center py-2 text-tg-button text-sm font-medium mb-2">
          Отпустите чтобы переместить
        </div>
      )}

      {/* Posts */}
      {day.posts.length > 0 ? (
        <div className="space-y-2">
          {day.posts.map((post) => (
            <PostCard
              key={post.id}
              post={post}
              onClick={() => onPostSelect(post)}
              onDragStart={onDragStart}
              onDragEnd={onDragEnd}
              isDragging={draggedPost?.id === post.id}
            />
          ))}
        </div>
      ) : (
        <div className="text-center py-4 text-tg-hint text-sm">
          {isDragOver ? 'Отпустите здесь' : 'Нет постов'}
        </div>
      )}
    </div>
  )
}

function PostCard({ post, onClick, onDragStart, onDragEnd, isDragging }) {
  const statusColors = {
    draft: 'bg-gray-200 text-gray-700',
    scheduled: 'bg-blue-100 text-blue-700',
    published: 'bg-green-100 text-green-700',
    error: 'bg-red-100 text-red-700',
  }

  const statusLabels = {
    draft: 'Черновик',
    scheduled: 'Запланирован',
    published: 'Опубликован',
    error: 'Ошибка',
  }

  const time = post.publish_at
    ? format(new Date(post.publish_at), 'HH:mm')
    : '—'

  return (
    <div
      onClick={onClick}
      className="bg-white rounded-lg p-3 cursor-pointer hover:shadow-md transition-shadow"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          {/* Time and status */}
          <div className="flex items-center gap-2 mb-1">
            <span className="text-sm font-medium text-tg-text">{time}</span>
            <span className={`text-xs px-2 py-0.5 rounded-full ${statusColors[post.status]}`}>
              {statusLabels[post.status]}
            </span>
          </div>

          {/* Text preview */}
          <p className="text-sm text-tg-text line-clamp-2">
            {post.text.replace(/<[^>]*>/g, '')}
          </p>

          {/* Platforms */}
          <div className="flex gap-1 mt-2">
            {post.platforms.map((p) => (
              <span key={p} className="text-xs text-tg-hint">
                {p === 'telegram' ? '📱 TG' : p === 'vk' ? '🔵 VK' : p}
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
