import base64
import csv
import io
import json
import re
import shlex
import subprocess
import threading
import webbrowser
from urllib.parse import urlparse

import customtkinter as ctk
from tkinter import filedialog, messagebox


ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

COURSE_FIELDS = ["fields", "name,id,alternatelink"]
DEFAULT_EMAIL_DOMAIN = "@qos.edu.hk"


def normalize_email(value):
    value = value.strip()
    if value and "@" not in value:
        return value + DEFAULT_EMAIL_DOMAIN
    return value


def split_emails(value):
    return [normalize_email(item) for item in value.split(",") if item.strip()]


def extract_course_id(output):
    match = re.search(r"\bCourse:\s*(\d+)", output)
    return match.group(1) if match else ""


def extract_emails_from_gam_csv(output):
    emails = []
    rows = list(csv.reader(io.StringIO(output)))
    email_index = None
    for row in rows:
        normalized = [value.strip().casefold() for value in row]
        if "email" in normalized:
            email_index = normalized.index("email")
            continue
        if email_index is None or len(row) <= email_index:
            continue
        value = row[email_index].strip()
        if "@" in value and " " not in value:
            emails.append(value)
    return list(dict.fromkeys(emails))


def normalize_course_id(value):
    value = value.strip()
    if not value.startswith(("http://", "https://")):
        return value

    path_parts = [part for part in urlparse(value).path.split("/") if part]
    try:
        token = path_parts[path_parts.index("c") + 1]
    except (ValueError, IndexError):
        return value

    try:
        padding = "=" * (-len(token) % 4)
        decoded = base64.urlsafe_b64decode(token + padding).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return token
    return decoded if decoded.isdigit() else token


class GAMRunner:
    def __init__(self, app):
        self.app = app

    def run(self, args):
        return_code, _ = self.execute(args)
        return return_code == 0

    def execute(self, args, display_output=True):
        command = "gam " + " ".join(shlex.quote(str(arg)) for arg in args)
        self.app.log(f"$ {command}")
        try:
            result = subprocess.run(
                ["gam", *args], capture_output=True, text=True,
                encoding="utf-8", errors="replace", shell=False,
            )
        except FileNotFoundError:
            self.app.log("❌ 找不到 GAM，請確認 gam 已加入 PATH。")
            return 1, ""

        output = (result.stdout or result.stderr).strip()
        if output and display_output:
            self.app.log(output)
        self.app.log("✅ 指令完成。" if result.returncode == 0 else f"❌ 指令失敗 (code {result.returncode})。")
        return result.returncode, output

    def execute_csv(self, csv_text, args):
        command = "gam csv - " + " ".join(shlex.quote(str(arg)) for arg in args)
        self.app.log(f"$ {command}")
        try:
            result = subprocess.run(
                ["gam", "csv", "-", *args],
                input=csv_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except FileNotFoundError:
            self.app.log("❌ 找不到 GAM，請確認 gam 已加入 PATH。")
            return False

        output = (result.stdout or result.stderr).strip()
        if output:
            self.app.log(output)
        self.app.log("✅ 批量指令完成。" if result.returncode == 0 else f"❌ 批量指令失敗 (code {result.returncode})。")
        return result.returncode == 0

    def add_users(self, course_id, role, emails):
        if not emails:
            return True
        csv_text = "email\n" + "\n".join(emails) + "\n"
        return self.execute_csv(csv_text, ["gam", "course", course_id, "add", role, "~email"])

    def add_group_members(self, course_id, group_email):
        return_code, output = self.execute(
            ["print", "group-members", "group", group_email, "recursive", "fields", "email"],
            display_output=False,
        )
        if return_code != 0:
            return False

        members = extract_emails_from_gam_csv(output)
        self.app.log(f"Group {group_email} 找到 {len(members)} 位成員。")
        if not members:
            return True
        return self.execute_csv(output, ["gam", "course", course_id, "add", "student", "~email"])


class ClassroomRow:
    def __init__(self, parent):
        self.frame = ctk.CTkFrame(parent)
        self.frame.pack(fill="x", padx=5, pady=5)
        self.entry_name = ctk.CTkEntry(self.frame, placeholder_text="課程名稱", width=170)
        self.entry_name.pack(side="left", padx=5)
        self.entry_teacher = ctk.CTkEntry(self.frame, placeholder_text="老師 Email", width=170)
        self.entry_teacher.pack(side="left", padx=5)
        self.entry_group = ctk.CTkEntry(self.frame, placeholder_text="學生 Group Email (可空)", width=180)
        self.entry_group.pack(side="left", padx=5)

    def get_data(self):
        return {
            "name": self.entry_name.get().strip(),
            "teacher": self.entry_teacher.get().strip(),
            "group": self.entry_group.get().strip(),
        }

    def destroy(self):
        self.frame.destroy()


class GAMClassroomGUI(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Google Classroom GAM 管理工具")
        self.geometry("900x680")
        self.minsize(780, 580)
        self.rows = []
        self.runner = GAMRunner(self)

        self.header = ctk.CTkFrame(self, fg_color="transparent")
        self.header.pack(fill="x", padx=24, pady=(18, 4))
        self.title_label = ctk.CTkLabel(self.header, text="Google Classroom 管理工具", font=("Arial", 24, "bold"))
        self.title_label.pack(side="left")
        self.back_button = ctk.CTkButton(self.header, text="← 返回主頁", width=120, command=self.show_home)

        self.content = ctk.CTkFrame(self, fg_color="transparent")
        self.content.pack(fill="both", expand=True, padx=24, pady=12)
        self.log_box = ctk.CTkTextbox(self, height=145)
        self.log_box.pack(fill="x", padx=24, pady=(0, 20))
        self.log("系統就緒。請從主頁選擇要執行的 Classroom 功能。")
        self.show_home()

    def clear_content(self):
        for child in self.content.winfo_children():
            child.destroy()

    def show_home(self):
        self.back_button.pack_forget()
        self.title_label.configure(text="Google Classroom 管理工具")
        self.clear_content()
        ctk.CTkLabel(self.content, text="選擇功能後輸入詳細資料", font=("Arial", 16)).pack(anchor="w", pady=(0, 14))
        menu = ctk.CTkFrame(self.content, fg_color="transparent")
        menu.pack(fill="x")
        actions = [
            ("查詢課程", "用課程名稱、老師 Email 或課程 ID／Link 搜尋", self.show_search),
            ("課程管理", "建立、更新或刪除課程", self.show_course_manage),
            ("課程成員", "加入或移除學生、老師及 Group 名單", self.show_member_manage),
            ("同步課程成員", "以 Group 或 CSV 名單同步學生", self.show_sync),
        ]
        for index, (name, description, command) in enumerate(actions):
            card = ctk.CTkFrame(menu)
            card.grid(row=index // 2, column=index % 2, padx=7, pady=7, sticky="nsew")
            menu.grid_columnconfigure(index % 2, weight=1)
            title = ctk.CTkLabel(card, text=name, font=("Arial", 17, "bold"))
            title.pack(anchor="w", padx=18, pady=(16, 3))
            detail = ctk.CTkLabel(card, text=description, text_color="gray")
            detail.pack(anchor="w", padx=18, pady=(0, 16))
            for widget in (card, title, detail):
                widget.bind("<Button-1>", lambda _event, action=command: action())

    def show_page(self, title):
        self.back_button.pack(side="right")
        self.title_label.configure(text=title)
        self.clear_content()

    def field(self, parent, label, placeholder="", width=420):
        ctk.CTkLabel(parent, text=label).pack(anchor="w", pady=(8, 3))
        entry = ctk.CTkEntry(parent, placeholder_text=placeholder, width=width)
        entry.pack(anchor="w")
        return entry

    def action_button(self, parent, text, command):
        ctk.CTkButton(parent, text=text, command=command, width=160).pack(anchor="w", pady=18)

    def choose_csv(self, entry):
        path = filedialog.askopenfilename(
            title="選擇 CSV 檔案",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            entry.delete(0, "end")
            entry.insert(0, path)

    def run_async(self, args, success_message="操作完成。"):
        def worker():
            success = self.runner.run(args)
            if success:
                self.after(0, lambda: messagebox.showinfo("完成", success_message))

        threading.Thread(target=worker, daemon=True).start()

    def show_search(self):
        self.show_page("查詢課程")
        panel = ctk.CTkFrame(self.content)
        panel.pack(fill="x", anchor="n")
        query = self.field(panel, "搜尋關鍵字", "課程名稱、老師 Email 或課程 ID / Link")
        search_type = ctk.StringVar(value="自動判斷")
        ctk.CTkLabel(panel, text="搜尋方式").pack(anchor="w", pady=(8, 3))
        ctk.CTkOptionMenu(
            panel,
            variable=search_type,
            values=["自動判斷", "課程 ID / Link", "老師 Email", "課程名稱"],
        ).pack(anchor="w")
        results = ctk.CTkScrollableFrame(self.content, height=260)
        results.pack(fill="both", expand=True, pady=(12, 0))

        def display_results(output, keyword=""):
            for child in results.winfo_children():
                child.destroy()
            courses = []
            for row in csv.reader(io.StringIO(output)):
                if len(row) < 2:
                    continue
                try:
                    item = json.loads(row[1])
                except json.JSONDecodeError:
                    continue
                if isinstance(item, dict):
                    courses.append(item)

            if courses and keyword:
                courses = [course for course in courses if keyword.casefold() in str(course.get("name", "")).casefold()]
            if not courses:
                ctk.CTkLabel(results, text="找不到符合條件的課程。", text_color="gray").pack(anchor="w", padx=12, pady=12)
                return

            ctk.CTkLabel(results, text=f"找到 {len(courses)} 個課程", font=("Arial", 15, "bold")).pack(anchor="w", padx=12, pady=(10, 6))
            for course in courses:
                name = course.get("name", "未命名課程")
                course_id = course.get("id", "")
                link = course.get("alternatelink", course.get("alternateLink", ""))
                row = ctk.CTkFrame(results)
                row.pack(fill="x", padx=8, pady=4)
                ctk.CTkLabel(row, text=f"{name}    ID: {course_id}", anchor="w").pack(side="left", padx=10, pady=8)
                if course_id:
                    ctk.CTkButton(
                        row,
                        text="複製 ID",
                        width=90,
                        command=lambda value=course_id: self.copy_to_clipboard(value),
                    ).pack(side="right", padx=4, pady=5)
                if link:
                    ctk.CTkButton(row, text="開啟 Classroom", width=130, command=lambda url=link: webbrowser.open(url)).pack(side="right", padx=10, pady=5)

        def search_courses(args, keyword=""):
            def worker():
                return_code, output = self.runner.execute([*args, "formatjson"], display_output=False)
                if return_code == 0:
                    self.after(0, lambda: display_results(output, keyword))

            threading.Thread(target=worker, daemon=True).start()

        def search():
            value = query.get().strip()
            if not value:
                messagebox.showwarning("提示", "請輸入搜尋關鍵字。")
                return

            mode = search_type.get()
            if mode == "自動判斷":
                if "@" in value:
                    mode = "老師 Email"
                elif value.isdigit() or value.startswith(("http://", "https://")):
                    mode = "課程 ID / Link"
                else:
                    mode = "課程名稱"
            if mode == "課程 ID / Link":
                search_courses(["print", "courses", "course", normalize_course_id(value), *COURSE_FIELDS])
            elif mode == "老師 Email":
                search_courses(["print", "courses", "teacher", normalize_email(value), *COURSE_FIELDS])
            else:
                search_courses(["print", "courses", *COURSE_FIELDS], value)

        self.action_button(panel, "🔎 開始查詢", search)

    def copy_to_clipboard(self, value):
        self.clipboard_clear()
        self.clipboard_append(value)
        self.update()
        self.log(f"已複製課程 ID：{value}")

    def show_create(self, parent=None):
        if parent is None:
            self.show_page("課程管理")
        panel = parent or ctk.CTkFrame(self.content)
        if parent is None:
            panel.pack(fill="both", expand=True)
        ctk.CTkLabel(panel, text="可新增多列，按一次建立全部有效課程。", text_color="gray").pack(anchor="w", padx=16, pady=(12, 4))
        rows_frame = ctk.CTkScrollableFrame(panel, height=250)
        rows_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self.rows = []

        def add_row():
            self.rows.append(ClassroomRow(rows_frame))

        def remove_row():
            if self.rows:
                self.rows.pop().destroy()

        controls = ctk.CTkFrame(panel, fg_color="transparent")
        controls.pack(fill="x", padx=10, pady=8)
        ctk.CTkButton(controls, text="+ 新增一班", command=add_row, width=120).pack(side="left", padx=5)
        ctk.CTkButton(controls, text="- 刪除最後一班", command=remove_row, width=130, fg_color="gray").pack(side="left", padx=5)

        def create():
            data = [row.get_data() for row in self.rows]
            valid = [item for item in data if item["name"] and item["teacher"]]
            if not valid:
                messagebox.showwarning("提示", "請至少填寫課程名稱與老師 Email。")
                return

            def worker():
                for item in valid:
                    teachers = split_emails(item["teacher"])
                    groups = split_emails(item["group"])
                    args = ["create", "course", "name", item["name"], "teacher", teachers[0], "status", "ACTIVE"]
                    return_code, output = self.runner.execute(args)
                    course_id = extract_course_id(output)
                    if return_code == 0 and course_id:
                        self.runner.add_users(course_id, "teacher", teachers[1:])
                        for group in groups:
                            self.runner.add_group_members(course_id, group)
                    elif return_code == 0:
                        self.log("⚠️ 課程已建立，但未能從 GAM 輸出讀取課程 ID，略過自動加人。")
                self.after(0, lambda: messagebox.showinfo("完成", "課程建立程序已完成。"))

            threading.Thread(target=worker, daemon=True).start()

        ctk.CTkButton(controls, text="🚀 建立全部課程", command=create, fg_color="green", width=150).pack(side="right", padx=5)
        add_row()
        add_row()

    def show_delete(self, parent=None):
        panel = parent or ctk.CTkFrame(self.content)
        if parent is None:
            self.show_page("課程管理")
            panel.pack(fill="x", anchor="n")
        course = self.field(panel, "課程 ID / Link", "例如 123456789 或 Classroom Link")
        ctk.CTkLabel(panel, text="刪除通常不可逆，建議先到「查詢課程」確認識別資料。", text_color="#e6a23c").pack(anchor="w", pady=(12, 0))

        def delete():
            value = normalize_course_id(course.get())
            if not value:
                messagebox.showwarning("提示", "請輸入課程 ID 或 Classroom Link。")
                return
            if messagebox.askyesno("確認刪除", f"確定要刪除課程「{value}」？"):
                self.run_async(["delete", "course", value], "課程刪除程序已完成。")

        self.action_button(panel, "🗑 刪除課程", delete)

    def show_add_people(self, parent=None):
        if parent is None:
            self.show_page("課程成員")
        panel = parent or ctk.CTkFrame(self.content)
        if parent is None:
            panel.pack(fill="x", anchor="n")
        course = self.field(panel, "課程 ID / Link", "課程 ID 或 Classroom Link")
        person = self.field(panel, "學生資料", "學生 Email、Group Email 或 CSV 檔案路徑")
        role = ctk.StringVar(value="student")
        source_type = ctk.StringVar(value="user")
        ctk.CTkLabel(panel, text="身分").pack(anchor="w", pady=(8, 3))
        ctk.CTkOptionMenu(panel, variable=role, values=["student", "teacher"]).pack(anchor="w")
        ctk.CTkLabel(panel, text="資料類型").pack(anchor="w", pady=(8, 3))
        ctk.CTkOptionMenu(panel, variable=source_type, values=["user", "group", "file"]).pack(anchor="w")
        ctk.CTkButton(panel, text="選擇 CSV 檔案", command=lambda: self.choose_csv(person), width=140).pack(anchor="w", pady=(8, 0))

        def add():
            course_value = normalize_course_id(course.get())
            source = person.get().strip()
            if not course_value or not source:
                messagebox.showwarning("提示", "請填寫課程與學生資料。")
                return
            if source_type.get() == "file":
                self.run_async(["course", course_value, "sync", role.get() + "s", "file", source], "加人程序已完成。")
                return

            values = split_emails(source)

            def worker():
                if source_type.get() == "group":
                    if role.get() == "student":
                        for value in values:
                            self.runner.add_group_members(course_value, value)
                    else:
                        self.log("⚠️ Group 只能用於加入學生，老師請改用 user。")
                else:
                    self.runner.add_users(course_value, role.get(), values)
                self.after(0, lambda: messagebox.showinfo("完成", "加人程序已完成。"))

            threading.Thread(target=worker, daemon=True).start()

        self.action_button(panel, "＋ 加入課程", add)

    def show_remove_people(self, parent=None):
        panel = parent or ctk.CTkFrame(self.content)
        if parent is None:
            self.show_page("課程成員")
            panel.pack(fill="x", anchor="n")
        course = self.field(panel, "課程 ID / Link", "課程 ID 或 Classroom Link")
        person = self.field(panel, "成員 Email", "學生或老師 Email")
        role = ctk.StringVar(value="student")
        ctk.CTkLabel(panel, text="身分").pack(anchor="w", pady=(8, 3))
        ctk.CTkOptionMenu(panel, variable=role, values=["student", "teacher"]).pack(anchor="w")

        def remove():
            course_value = normalize_course_id(course.get())
            person_value = person.get().strip()
            if not course_value or not person_value:
                messagebox.showwarning("提示", "請填寫課程與成員 Email。")
                return
            values = split_emails(person_value)

            def worker():
                for value in values:
                    self.runner.run(["course", course_value, "remove", role.get() + "s", value])
                self.after(0, lambda: messagebox.showinfo("完成", "成員移除程序已完成。"))

            threading.Thread(target=worker, daemon=True).start()

        self.action_button(panel, "移除成員", remove)

    def show_member_manage(self):
        self.show_page("課程成員")
        tabs = ctk.CTkTabview(self.content)
        tabs.pack(fill="both", expand=True)
        tabs.add("加入成員")
        tabs.add("移除成員")
        self.show_add_people(tabs.tab("加入成員"))
        self.show_remove_people(tabs.tab("移除成員"))

    def show_sync(self):
        self.show_page("同步課程成員")
        panel = ctk.CTkFrame(self.content)
        panel.pack(fill="x", anchor="n")
        course = self.field(panel, "課程 ID / Link", "課程 ID 或 Classroom Link")
        source = self.field(panel, "同步來源", "Group Email；CSV 請按按鈕選擇")
        source_type = ctk.StringVar(value="group")
        ctk.CTkLabel(panel, text="來源類型").pack(anchor="w", pady=(8, 3))
        ctk.CTkOptionMenu(panel, variable=source_type, values=["group", "file"]).pack(anchor="w")
        ctk.CTkButton(panel, text="選擇 CSV 檔案", command=lambda: self.choose_csv(source), width=140).pack(anchor="w", pady=(8, 0))
        ctk.CTkLabel(panel, text="同步會以來源名單為準，請先備份或確認名單內容。", text_color="#e6a23c").pack(anchor="w", pady=(12, 0))

        def sync():
            course_value = normalize_course_id(course.get())
            source_value = source.get().strip()
            if not course_value or not source_value:
                messagebox.showwarning("提示", "請填寫課程與同步來源。")
                return
            if source_type.get() == "file":
                self.run_async(["course", course_value, "sync", "students", "file", source_value], "成員同步程序已完成。")
                return
            groups = split_emails(source_value)
            self.run_async(
                ["course", course_value, "sync", "students", "groups", ",".join(groups)],
                "成員同步程序已完成。",
            )

        self.action_button(panel, "⟳ 同步成員", sync)

    def show_course_manage(self):
        self.show_page("課程管理")
        tabs = ctk.CTkTabview(self.content)
        tabs.pack(fill="both", expand=True)
        tabs.add("建立課程")
        tabs.add("更新課程")
        tabs.add("刪除課程")
        self.show_create(tabs.tab("建立課程"))
        self.show_delete(tabs.tab("刪除課程"))

        update_panel = tabs.tab("更新課程")
        course = self.field(update_panel, "課程 ID / Link", "例如 123456789 或 Classroom Link")
        name = self.field(update_panel, "新課程名稱（可選）", "留空代表不更新")
        teacher = self.field(update_panel, "新老師 Email（可選）", "留空代表不更新")
        description = self.field(update_panel, "新課程描述（可選）", "留空代表不更新")
        room = self.field(update_panel, "新課室（可選）", "留空代表不更新")
        status = ctk.StringVar(value="不更新")
        ctk.CTkLabel(update_panel, text="課程狀態").pack(anchor="w", pady=(8, 3))
        ctk.CTkOptionMenu(update_panel, variable=status, values=["不更新", "ACTIVE", "ARCHIVED", "PROVISIONED"]).pack(anchor="w")

        def update():
            course_value = normalize_course_id(course.get())
            if not course_value:
                messagebox.showwarning("提示", "請輸入課程 ID 或 Classroom Link。")
                return
            attributes = []
            for key, entry in (("name", name), ("teacher", teacher), ("description", description), ("room", room)):
                value = entry.get().strip()
                if value:
                    if key == "teacher":
                        value = normalize_email(value)
                    attributes += [key, value]
            if status.get() != "不更新":
                attributes += ["status", status.get()]
            if not attributes:
                messagebox.showwarning("提示", "請至少填寫一項要更新的資料。")
                return
            self.run_async(["update", "course", course_value, *attributes], "課程更新完成。")

        self.action_button(update_panel, "更新課程", update)

    def log(self, text):
        self.after(0, lambda: (self.log_box.insert("end", text + "\n"), self.log_box.see("end")))


if __name__ == "__main__":
    app = GAMClassroomGUI()
    app.mainloop()
