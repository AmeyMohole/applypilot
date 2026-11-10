Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "C:\Users\Amey\Desktop\Interview-apply\jobscan"
sh.Run """C:\Users\Amey\AppData\Local\Programs\Python\Python312\python.exe"" run_daily.py --startup", 0, False
