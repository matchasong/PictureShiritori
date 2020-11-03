#coding: UTF-8
import os
import slack
from datetime import datetime
from dateutil.relativedelta import relativedelta
from logging import getLogger,StreamHandler,Formatter,DEBUG,INFO,WARNING,ERROR,CRITICAL
import json
import math
import boto3
import requests
import codecs
import time
import traceback
import hmac
import hashlib
import urllib
from datetime import datetime

logger = getLogger(__name__)
logger.setLevel(DEBUG)
loghandler = StreamHandler()
loghandler.setFormatter(Formatter("%(asctime)s %(name)s %(levelname)8s %(message)s"))
logger.addHandler(loghandler)

logger.info('処理を開始します')

slack_api_token = os.environ['SLACK_API_TOKEN']
client = slack.WebClient(token=os.environ['SLACK_API_TOKEN'])
bot_client = slack.WebClient(token=os.environ['SLACK_BOT_API_TOKEN'])
slack_signing_secret = os.environ['SLACK_SIGNING_SECRET']
post_channel = os.environ['POST_CHANNEL']
post_channel_id = os.environ['POST_CHANNEL_ID']
put_bucket = os.environ['PUT_BACKET']
time_stamp = datetime.now().strftime('%Y%m%d%H%M%S%f')
tmp_dir = '/tmp/' + time_stamp
TWO_DAYS = 180000 # 若干余裕を持っている

logger.info('環境変数を設定しました')

def handler(event, lambda_context):    
    try:

        logger.debug(f'event:{event}')
        body = event['body']
        logger.debug(f'event[body]:{body}')

        #リクエストの検証
        timestamp = event['headers']['X-Slack-Request-Timestamp']
        if abs(math.floor(time.time()) - int(timestamp)) > 60 * 5:
            logger.warning('reply attackの可能性があります')
            logger.info('処理を終了します')
            return

        logger.debug(f'timestamp:{timestamp}')

        body_str = str(body).replace(' ','').replace("'",'"')
        sig_basestring = f'v0:{timestamp}:{body_str}'

        logger.debug(f'sig_basestring:{sig_basestring}')
        logger.debug(f'slack_signing_secret:{slack_signing_secret}')
        my_signature = 'v0=' + hmac.new(slack_signing_secret.encode(),sig_basestring.encode(),hashlib.sha256).hexdigest()

        slack_signature = event['headers']['X-Slack-Signature']

        logger.debug(f'my_signature   :{my_signature}')
        logger.debug(f'slack_signature:{slack_signature}')

        if hmac.compare_digest(my_signature, slack_signature):
            logger.info('リクエスト検証 OK')
        else:
            logger.error('リクエスト検証 NG')
            logger.error('処理を中止します')
            return

        #Slack APIのチャレンジ用
        if isinstance(event['body'],str): #チャレンジの場合はbodyの要素はstr/それ以外はbodyの要素はdict
            if json.loads(event['body']).get('type',None) == 'url_verification':

                body_json = {
                        'challenge':json.loads(event['body']).get('challenge'),
                    }

                logger.debug('body_json:'+str(body_json))
                logger.debug('body_json:'+str(type(body_json)))

                response = {
                    "isBase64Encoded": False,
                    "statusCode": 200,
                    "headers": {},
                    "body": json.dumps(body_json)
                }

                return response
        
        
        #実行中のゲームがない場合は早期リターン
        if not is_game_in_progress():
            message_nogame = '実行中のゲームがありません'
            logger.info(message_nogame)
            #bot_client.chat_postMessage(channel=post_channel, text=message_nogame)
            return

        #受付メッセージを送信
        logger.info('受付済みのmessageを投稿します')
        message_accept = 'しりとり判定中です...しばらくお待ちください'
        bot_client.chat_postMessage(channel=post_channel, text=message_accept)
        message_accept_2 = '(他のチャンネルへの画像投稿を拾うことがありますが、自動で処理中断されます)'
        bot_client.chat_postMessage(channel=post_channel, text=message_accept_2)
        logger.info('受付済みのmessageを投稿しました')

        #イベントの画像IDを取得
        logger.debug('event[body][event]:'+str(event['body']['event']))
        file_id = event['body']['event']['file_id']

        #処理済みの画像だった場合は早期リターン
        if is_file_duplicated(file_id):
            message_duplicated = 'すでに画像'+str(file_id)+'は処理済みです'
            logger.info(message_duplicated)
            return

        #files.infoを呼び出す
        files_info = client.files_info(file=file_id)
        logger.debug('files_info:' + str(files_info))

        #ファイルチェック
        if not check_file(files_info):
            logger.error('処理を中止します')
            return

        #ユーザー名と、パスを取得
        poster = files_info['file']['user']
        image_file_path = files_info['file']['url_private_download']
        image_file_name = os.path.basename(image_file_path)

        logger.debug('poster:' + poster)
        logger.debug('image_file_path:' + image_file_path)
        logger.debug('image_file_name:' + image_file_name)

        #チャンネルが処理対象か否かを判定
        if not channel_check(file_id):
            message_other_channel='対象外チャンネルへの投稿のため処理を終了します'
            logger.info(message_other_channel)
            bot_client.chat_postMessage(channel=post_channel, text=message_other_channel)
            return

        #画像をslackから/tmpにダウンロードする
        if download_image_from_slack(image_file_path,tmp_dir):
            logger.info('画像の取得に成功しました')
        else:
            logger.error('画像の取得に失敗しました')
            exit(1)

        #画像にIDを振る マイクロ秒単位で同じでないとかぶらない。
        image_id = int(time_stamp)
        logger.debug('image_id' + str(image_id))

        #S3に画像を投稿
        file_path_local = tmp_dir + r'/' + image_file_name
        file_name_upload = str(image_id) + '.png'

        if upload_image_to_s3(file_path_local,put_bucket,file_name_upload):
            logger.info('S3に画像を投稿しました')
        else:
            logger.error('S3への画像の投稿に失敗しました')
            exit(1)
        
        #画像IDと送信者をDBに格納
        if insert_image_table(image_id,poster,file_id) :
            logger.info('imageテーブルに登録しました')
        else:
            logger.error('imageテーブルへの登録に失敗しました')
            exit(1)    

        #処理終了のログを出力
        logger.info('処理を終了します.')

    except Exception:
        message_error = f'エラーが発生しました:{traceback.format_exc()}'
        logger.error('message_error')
        bot_client.chat_postMessage(channel=post_channel, text=message_error)
        logger.error('処理を中止します')
        exit(1)


def insert_image_table(image_id,poster,file_id):
    """
    DynamoDBのimageテーブルにデータを挿入する

    param:     image_id int       画像ID
               poster   str       投稿者
               file_id int        (Slackの)ファイルID
    return:    ret      boolean   データ挿入成否

    """

    logger.info('Function insert_image_table')
    logger.info('image_id:'+str(image_id))
    logger.info('poster:'+poster)
    logger.info('image_id:'+str(file_id))

    dynamodb = boto3.resource('dynamodb')
    image_table = dynamodb.Table('image')

    dt = datetime.now()
    current_time = math.floor(dt.timestamp())

    item = {
        'imageId':image_id,
        'poster':poster,
        'fileId':file_id,
        'unixTime':int(current_time)+TWO_DAYS,
    }

    try:
        res = image_table.put_item(Item=item)
        logger.info(res)
    except Exception as e:
        logger.error(e)
        return False
    
    return True


def upload_image_to_s3(file_name,bucket,object_name):
    """
    ファイルをS3にアップする

    param:    file_name  str       ファイル名
              bucket     str       バケット名
              object_name str      オブジェクト名
    retern:   ret        boolean   データ挿入成否

    """
    logger.info('Function upload_image_to_s3')
    logger.info('file_name:'+file_name)
    logger.info('bucket:'+bucket)
    logger.info('object_name:'+object_name)

    s3_client = boto3.client('s3')
    try:
        s3_client.upload_file(file_name, bucket, object_name)
    except Exception as e:
        logger.error(f'画像をS3にアップロードできませんでした:{e}')
        return False
    return True


def download_image_from_slack(from_url,to_path):
    """
    画像をSlackからダウンロードする
    param: from_url     str        画像のダウンロード元
           to_path      str        画像のダウンロード先
    return: image_content binary   画像
    """

    logger.info('Function download_image_from_slack')
    logger.info('from_url:'+from_url)
    logger.info('to_path:'+to_path)

    if not os.path.exists(to_path):
        os.mkdir(to_path)
    
    image_content = None
    try:
        image_content = requests.get(
                from_url,
                allow_redirects = True,
                headers = {'Authorization':f'Bearer {slack_api_token}'},
                stream = True
        ).content

        target_path = to_path + r'/' + os.path.basename(from_url)

        with codecs.open(target_path,mode='wb') as target:
            target.write(image_content)

    except Exception as e:
        logger.error(f'画像の書き込みに失敗しました:{e}')
        return False

    return True

def channel_check(file_id):
    """
    ファイルが、対象チャンネルに投稿されているかかどうか判断する
    param: file_id          str        ファイルID
    return:isValid          boolean    対象チャンネル(True),対象外(False)
    """

    logger.info('channel_check')
    logger.info('file_id:'+file_id)

    valid_channel = post_channel_id
    logger.info('valid_channel:'+valid_channel)

    ts_from = round(time.time()) - 300 #5分間
    logger.info('ts_from:'+str(ts_from))

    for i in range(6):
    
        logger.info(f'files_listループ{i+1}回目')
        #対象チャンネルに投稿されているファイル一覧を取得(5分前まで遡る)
        files_list = client.files_list(channel=valid_channel,ts_from=ts_from)
        logger.debug('files_list:'+str(files_list))

        try:
            for f in files_list.get('files'):
                if f['id'] == file_id:
                    logger.info('対象チャンネルの投稿です')
                    return True
        except Exception as e:
            logger.warning(f'files_listにchannnelのidがありません:[files_info]{files_list}:[エラーメッセージ]:{e}')
            return False
        
        time.sleep(10) #投稿したファイルがfiles.listで読み出せるようになるまでにラグがあるので待つ。

    return False
    
    
def is_game_in_progress():
    """
    実行中のゲームがあるか調べる
    
    引数    :  なし
    戻り値  :  isGame boolean 実行中のゲームがある(True)か、ない(False)か。
    """

    logger.info('Function is_game_in_progress')

    dynamodb = boto3.resource('dynamodb')
    game_table = dynamodb.Table('game')

    try:
        res = game_table.scan()
        logger.info(res)

    except Exception as e:
        logger.warning(f'gameテーブルへの接続に失敗しました:{e}')
        raise e

    for item in res.get('Items'):
        if not item.get('isEnded'):
            logger.debug('実行中のゲームがあります')
            return True

    logger.debug('実行中のゲームはありません')
    return False


def is_file_duplicated(file_id):
    """
    Slackのfile_idが投稿済みでないか調べる(多重実行防止)
    
    引数　  :  file_id          str        ファイルID
    戻り値  :  ret              boolean    投稿済み(true),未投稿(false)
    """

    logger.info('Function is_file_duplicated')
    logger.info('file_id:'+str(file_id))

    dynamodb = boto3.resource('dynamodb')
    image_table = dynamodb.Table('image')

    try:
        res = image_table.scan()
        logger.info(res)

    except Exception as e:
        logger.error(f'imageテーブルへの接続に失敗しました:{e}')
        raise e

    for item in res.get('Items'):
        if item.get('fileId') == file_id:
            logger.warning('この画像は処理済みです')
            return True


    logger.info('画像は未処理です')
    return False
    

def check_file_suffix(file_name):
    """
    ファイル名の拡張子チェック
    
    引数　  :  file_name        str        ファイル名
    戻り値  :  ret              boolean    チェックOK(true),NG(false)
    """

    logger.info('Start Function check_file_suffix')
    logger.info(f'file_name:{file_name}')

    allow_suffixes = ['png','jpg','jpeg']

    suffix_index = file_name.rfind('.')
    file_suffix = file_name[suffix_index+1:]

    if file_suffix in allow_suffixes:
        logger.info('End Function check_file_suffix')
        return True
    else:
        logger.info('End Function check_file_suffix')
        return False

    
def check_file_size(files_info):
    """
    ファイルのサイズチェック
    
    引数　  :  files_info       dict       SlackAPI(files_info)の戻り値
    戻り値  :  ret              boolean    チェックOK(true),NG(false)
    """

    logger.info('Start Function check_file_size')
    logger.debug(f'files_info:{files_info}')

    file_name = files_info['file']['name']
    logger.info(f'file_name:{file_name}')

    file_size = files_info['file']['size']
    logger.info(f'file_size:{file_size}')

    max_size = 5000000 #5MB(AWS rekognitionの上限)

    if file_size <= max_size:
        logger.info('End Function check_file_size')
        return True
    else:
        logger.info('End Function check_file_size')
        return False


def check_file(files_info):
    """
    ファイルのチェック処理
    
    引数　  :  files_info       dict       SlackAPI(files_info)の戻り値
    戻り値  :  ret              boolean    チェックOK(true),NG(false)
    """

    logger.info('Start Function check_file')
    logger.debug(f'files_info:{files_info}')

    file_name = files_info['file']['name']
    logger.info(f'file_name:{file_name}')

    #ファイルサイズチェック
    if not check_file_size(files_info):
        message_exceeded = f'ファイルサイズが大きすぎます:{file_name}'
        logger.error(message_exceeded)
        bot_client.chat_postMessage(channel=post_channel, text=message_exceeded)
        return False
    
    #ファイル拡張子チェック
    if not check_file_suffix(file_name):
        message_suffix_invalid = f'拡張子が.png/.jpg/.jpeg以外の画像は処理できません:{file_name}'
        logger.error(message_suffix_invalid)
        bot_client.chat_postMessage(channel=post_channel, text=message_suffix_invalid)
        return False
    
    #files_info['file']['url_private_download']のキー存在チェック
    try:
        files_info.get('file').get('url_private_download')
    except:
        #files_info['file']['url_private_download']のキーが存在しない場合→ダウンロードできない
        message_nokey = f'画像を処理できませんでした:{file_name}'
        logger.error(message_nokey)
        bot_client.chat_postMessage(channel=post_channel, text=message_nokey)
        return False
    
    logger.info('ファイルチェックOK')
    logger.info('End Function check_file')
    return True