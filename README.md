# PictureShiritori
Slackで遊ぶ、絵しりとりアプリ

※AWSの料金がかかりますので、ご注意ください。

【前提条件】

- AWSアカウントを設定しておく

- Slackワークスペースを作成し、投稿先のチャンネルも作成しておく

- Python,pip,npm,serverlessをインストールしておく

【アプリ設定】

１.Slackの設定

(1)SlackAPPとbotユーザーを作成<br>

(2)Slackのスラッシュコマンド作成<br>
   作成したAPPの、Add features and functionality　-> Slash Command -> Create New Command から<br>
   スラッシュコマンドを作成する(例:/shiritori)<br>
 
(3)権限付与<br>
   作成したAPPの、Add features and functionality　-> Permissions -> Scopes を開いて、<br>
   Bot Token Scopes に、chat:write,commands,files:read<br>
   User Token Scopesに、channels:write,chat:write,files:read,users.profile:read<br>
   を付与しておく<br>

(4)Event Subscriptionsの設定<br>
    作成したAPPの、Add features and functionality　-> Event Subscriptionsを開いて、Enable EventsをONにする<br>
    Subscribe to bot events欄で"Add Bot User Event"をクリックして、プルダウンから"file_shared"を選択<br>
   
(5)APPのインストール<br>
    Install your app to your workspace-> "Reinstall App"をクリック
  

２.myCustomFile.ymlの設定

プロジェクトのルートディレクトリ直下に、myCustomFile.ymlを以下の内容で作成して保存する

```yml
[myCustomFile.yml]
slack_token:<SlackのAPIトークン>
slack_bot_token:<SlackのBOTのAPIトークン>
post_channel:<投稿先チャンネル名>
post_channel_id:<投稿先チャンネルID>
deployment_bucket:<デプロイ用バケット名>
put_bucket:<画像格納用バケット名>
aws_account_id:<AWSアカウントのID>
slack_signing_secret:<Slackのsigning_secret>
```

3.デプロイ用バケットの作成

<deployment_bucket>の名前でS3バケットを作成する

4.モジュールを、Slack APIチャレンジ対応用に編集

serverlessのasync項目がtrueの行をコメントアウト、<br>
falseの行を有効にする

```yml
[serverless.yml]
      - http:
          path: putImageToS3
          method: post
#          async: true
          async: false #for Slack API challenge
```

5.Slack APIチャレンジ対応用をデプロイ
(1)npmインストール
```shell
npm install
```
(2)serverlessデプロイ
```
sls -v deploy
```

6.SlackのAPPにデプロイ先のURLを設定する

(1)Slash CommandのURLを設定

- Add features and functionality　-> Slash Command -> ２で設定したスラッシュコマンドを編集(ない場合はCreate New Commandから作成する)<br>
- Request URL欄に`https://XXXXXXXXXX/execute-api.<リージョン名>.amazonaws.com/dev/gameStart`のエンドポイントを貼り付ける<br>
- "Save"をクリック

(2)Event APIのURLを設定

- Add features and functionality　-> Event Subscriptions<br>
- Enable EventsをOnにする<br>
- Request URL欄に`https://XXXXXXXXXX/execute-api.<リージョン名>.amazonaws.com/dev/putImageToS3`のエンドポイントを貼り付ける<br>
- "Verified"となったら、"Save Changes"をクリック

(3)Appを再インストール
Install your app to your workspace　-> "ReInstall App"をクリック

7.モジュールを、本番用に再編集(4.の編集内容を戻す)
serverlessのasync項目がtrueの行を有効にし、falseの行をコメントアウトする

```yml
[serverless.yml]
      - http:
          path: putImageToS3
          method: post
          async: true
#          async: false #for Slack API challenge
```

8.本番用をデプロイ
```shell
sls -v deploy
```

9.AWSコンソールにログインし、S3の権限を、「バケットとオブジェクトは非公開」に設定する。

【遊び方】

1.Slackにスラッシュコマンドを発行するとゲームを開始します

```
/shiritori [制限時間(１〜４８)h]
```

Slackに投稿される開始メッセージ例
```
AI絵しりとりを始めるよ！
最初の文字は...S。
締め切りは今から１時間後、ゲームスタート！
```


2.画像をSlackに投稿すると、しりとり(英語)成立か不成立かが表示されます

<成立の例>
```
Good！それはAircraftだね。次の文字はTだよ！
```

<不成立の例1　しりとりが成立していない時>
```
残念！ それはLeisure Activitiesだね。Aで始まるものを描いてね！
```

<不成立の例２　判定結果の単語がすでに出ている時>
```
残念！ それはLampだね。もう出ているよ！
```


3.制限時間が経過するとゲームを終了します
```
今回のしりとりは Apple -> Egg -> Gold だったよ！
優勝者は...ほげ田ふが太郎、おめでとう！
また挑戦してねー
```
