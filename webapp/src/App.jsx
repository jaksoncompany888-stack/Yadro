import { useState, useEffect } from 'react'
import Calendar from './components/Calendar'
import PostEditor from './components/PostEditor'
import { useTelegram } from './hooks/useTelegram'
import { api } from './api/client'

function App() {
  const { tg, user, initData } = useTelegram()
  const [view, setView] = useState('calendar') // calendar | editor | post
  const [selectedDate, setSelectedDate] = useState(null)
  const [selectedPost, setSelectedPost] = useState(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    // Initialize Telegram Mini App
    if (tg) {
      tg.ready()
      tg.expand()

      // Set header color
      tg.setHeaderColor('secondary_bg_color')

      // Back button handler
      tg.BackButton.onClick(() => {
        if (view !== 'calendar') {
          setView('calendar')
          setSelectedPost(null)
          tg.BackButton.hide()
        }
      })
    }

    setLoading(false)
  }, [tg, view])

  // Show back button when not on calendar
  useEffect(() => {
    if (tg) {
      if (view !== 'calendar') {
        tg.BackButton.show()
      } else {
        tg.BackButton.hide()
      }
    }
  }, [view, tg])

  const handleDateSelect = (date) => {
    setSelectedDate(date)
    setView('editor')
  }

  const handlePostSelect = (post) => {
    setSelectedPost(post)
    setView('post')
  }

  const handleCreatePost = () => {
    setSelectedPost(null)
    setSelectedDate(new Date())
    setView('editor')
  }

  const handleSave = async (postData) => {
    try {
      if (selectedPost) {
        await api.updatePost(selectedPost.id, postData, initData)
      } else {
        await api.createPost(postData, initData)
      }
      setView('calendar')
      setSelectedPost(null)
    } catch (error) {
      console.error('Failed to save post:', error)
      tg?.showAlert('Ошибка сохранения')
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-screen">
        <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-tg-button"></div>
      </div>
    )
  }

  return (
    <div className="safe-area min-h-screen bg-tg-bg">
      {/* Header */}
      <header className="sticky top-0 bg-tg-secondary-bg px-4 py-3 z-10">
        <div className="flex items-center justify-between">
          <h1 className="text-lg font-semibold text-tg-text">
            {view === 'calendar' ? 'Календарь' : view === 'editor' ? 'Новый пост' : 'Редактирование'}
          </h1>
          {view === 'calendar' && (
            <button
              onClick={handleCreatePost}
              className="bg-tg-button text-tg-button-text px-4 py-2 rounded-lg text-sm font-medium"
            >
              + Создать
            </button>
          )}
        </div>
      </header>

      {/* Content */}
      <main className="p-4">
        {view === 'calendar' && (
          <Calendar
            onDateSelect={handleDateSelect}
            onPostSelect={handlePostSelect}
            initData={initData}
          />
        )}

        {(view === 'editor' || view === 'post') && (
          <PostEditor
            post={selectedPost}
            date={selectedDate}
            onSave={handleSave}
            onCancel={() => {
              setView('calendar')
              setSelectedPost(null)
            }}
            initData={initData}
          />
        )}
      </main>
    </div>
  )
}

export default App
