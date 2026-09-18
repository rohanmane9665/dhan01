import os
import sys
import asyncio
from datetime import datetime, timedelta
sys.path.append('d:/PROJECTS/Freelancing/Dhann Rohan/backend')
from dotenv import load_dotenv
load_dotenv()
from app.dhan.client import get_dhan_client
import pytz
IST = pytz.timezone('Asia/Kolkata')

async def main():
    dhan = get_dhan_client()
    dhan.initialize()
    to_date = datetime.now(IST)
    from_date = to_date - timedelta(days=5)
    print("Dates:", from_date.strftime('%Y-%m-%d'), to_date.strftime('%Y-%m-%d'))
    result = await dhan.intraday_minute_data(
        '13', 'IDX_I', 'INDEX',
        from_date.strftime('%Y-%m-%d'),
        to_date.strftime('%Y-%m-%d'),
        5
    )
    print("Result keys:", result.keys() if isinstance(result, dict) else result)
    if isinstance(result, dict) and 'status' in result:
        print("Status:", result['status'])
        if 'data' in result and isinstance(result['data'], dict):
            print("Data points:", len(result['data'].get('close', [])))
        else:
            print("Data:", result.get('data'))
            print("Remarks:", result.get('remarks'))

if __name__ == '__main__':
    asyncio.run(main())
