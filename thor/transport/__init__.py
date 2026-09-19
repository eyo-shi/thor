"""共通 HTTP / 認証 / ロギング層。

Knox JWT / IDBroker STS 資格情報の伝搬をここで受け持ち、
Tool は user_context を経由してエンドユーザー権限で外部システムを叩く。
"""
