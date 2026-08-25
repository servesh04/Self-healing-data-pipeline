import { postGlobalChat } from '../../api/client'
import ChatPanel from '../RunChat/ChatPanel'

// Per the feature spec's frontend requirements: panel on Overview, below
// Specialist Performance, with these 3 chips specifically (cross-run
// questions per-run chat cannot answer at all).
const SUGGESTED = [
  'Which drift class is hardest to heal?',
  'Is the confidence threshold well calibrated?',
  'Summarize the last 20 runs',
]

export default function GlobalChat() {
  return (
    <ChatPanel
      suggested={SUGGESTED}
      placeholder="Ask about the whole pipeline…"
      onSend={(messages) => postGlobalChat(messages)}
    />
  )
}
