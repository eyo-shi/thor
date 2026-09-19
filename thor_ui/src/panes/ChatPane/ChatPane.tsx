/**
 * 右ペイン: エージェント対話。
 * SSE で流れてくる step / token / artifact / error / done を Store に反映し、
 * MessageList / StepIndicator が render する。
 */
import { MessageList } from "./MessageList";
import { PromptInput } from "./PromptInput";
import { StepIndicator } from "./StepIndicator";
import { useWishStream } from "./useWishStream";

export function ChatPane() {
  const wish = useWishStream();
  return (
    <div className="chat-pane">
      <div className="pane-header">
        <span>Chat</span>
      </div>
      <div className="pane-body chat-body">
        <MessageList />
        <StepIndicator />
      </div>
      <PromptInput wish={wish} />
    </div>
  );
}
