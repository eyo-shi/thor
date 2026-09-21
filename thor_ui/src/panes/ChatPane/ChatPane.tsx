/**
 * 右ペイン: エージェント対話。
 * SSE で流れてくる step / token / artifact / error / done を Store に反映し、
 * MessageList / StepIndicator が render する。
 *
 * Deploy 後の設定不足 (LLM / Trino / CDV) を示す HTTP 503 が返った場合は、
 * chatStore.setupError に格納された :class:`SetupGuideError` を SetupGuide
 * カードとして表示し、Project → Settings → Environment で env を追加して
 * Application を再起動する手順を提示する。
 */
import { useChatStore } from "../../stores/chatStore";
import { MessageList } from "./MessageList";
import { PromptInput } from "./PromptInput";
import { SetupGuide } from "./SetupGuide";
import { StepIndicator } from "./StepIndicator";
import { useWishStream } from "./useWishStream";

export function ChatPane() {
  const wish = useWishStream();
  const setupError = useChatStore((s) => s.setupError);
  const setSetupError = useChatStore((s) => s.setSetupError);
  return (
    <div className="chat-pane">
      <div className="pane-header pane-header--chat">
        <div className="pane-header__brand">
          <img
            src="/thor_logo.svg"
            alt=""
            className="pane-header__thor-icon"
          />
          <span className="pane-header__title">Thor</span>
        </div>
        <span className="pane-header__hint">自然言語でデータ操作を依頼</span>
      </div>
      <div className="chat-scroll">
        <MessageList />
        <StepIndicator />
        {setupError && (
          <SetupGuide
            error={setupError}
            onDismiss={() => setSetupError(null)}
          />
        )}
      </div>
      <PromptInput wish={wish} />
    </div>
  );
}
