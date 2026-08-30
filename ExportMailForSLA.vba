' ExportMailForSLA.vba
'
' Exports mail metadata from Outlook to two CSV files for the SLA reply-time
' report. Runs inside your already-signed-in Outlook client, so it needs no
' Graph permissions and no admin consent.
'
' Message bodies are never read. Only: conversation id, timestamp, sender,
' recipients, subject.
'
' HOW TO RUN
'   1. Open classic Outlook.
'   2. Press Alt+F11 to open the VBA editor.
'   3. Insert > Module.
'   4. Paste this whole file in.
'   5. Edit OUT_DIR below if your folder differs.
'   6. Press F5.
'
' It writes inbox.csv and sent.csv, then tells you how many rows it wrote.

Option Explicit

' ---- settings -------------------------------------------------------------

Const OUT_DIR As String = "C:\Users\Thaddeus\sla\"
Const START_DATE As String = "2025-07-01"
Const END_DATE As String = "2026-06-30"

' ---------------------------------------------------------------------------

Public Sub ExportMailForSLA()
    Dim nInbox As Long, nSent As Long

    On Error GoTo Fail

    nInbox = ExportFolder(olFolderInbox, OUT_DIR & "inbox.csv", True)
    nSent = ExportFolder(olFolderSentMail, OUT_DIR & "sent.csv", False)

    MsgBox "Done." & vbCrLf & vbCrLf & _
           "inbox.csv:  " & nInbox & " messages" & vbCrLf & _
           "sent.csv:   " & nSent & " messages" & vbCrLf & vbCrLf & _
           "Saved in " & OUT_DIR, vbInformation
    Exit Sub

Fail:
    MsgBox "Failed: " & Err.Description & vbCrLf & vbCrLf & _
           "If this is a 'path not found' error, create the folder in " & _
           "OUT_DIR first, or change OUT_DIR to a folder that exists.", vbCritical
End Sub


Private Function ExportFolder(folderId As Long, outPath As String, _
                              isInbox As Boolean) As Long
    Dim ns As Object, fld As Object, items As Object, itm As Object
    Dim fso As Object, ts As Object
    Dim filt As String, dateField As String
    Dim n As Long, stamp As Date

    Set ns = Application.GetNamespace("MAPI")
    Set fld = ns.GetDefaultFolder(folderId)
    Set items = fld.items

    ' Restrict by date up front — much faster than filtering in the loop.
    If isInbox Then
        dateField = "[ReceivedTime]"
    Else
        dateField = "[SentOn]"
    End If

    filt = dateField & " >= '" & Format(CDate(START_DATE), "ddddd h:nn AMPM") & "' AND " & _
           dateField & " <= '" & Format(CDate(END_DATE) + 1, "ddddd h:nn AMPM") & "'"

    On Error Resume Next
    Set items = items.Restrict(filt)
    On Error GoTo 0

    Set fso = CreateObject("Scripting.FileSystemObject")
    Set ts = fso.CreateTextFile(outPath, True, True)   ' True = unicode

    ts.WriteLine "conversation_id,timestamp,sender,recipients,subject"

    For Each itm In items
        If TypeName(itm) = "MailItem" Then
            On Error Resume Next

            stamp = Null
            If isInbox Then
                stamp = itm.ReceivedTime
            Else
                stamp = itm.SentOn
                If stamp = 0 Then stamp = itm.CreationTime
            End If

            If Err.Number = 0 And stamp > 0 Then
                ts.WriteLine _
                    CsvCell(itm.ConversationID) & "," & _
                    CsvCell(Format(stamp, "yyyy-mm-dd hh:nn:ss")) & "," & _
                    CsvCell(SenderSmtp(itm)) & "," & _
                    CsvCell(RecipientList(itm)) & "," & _
                    CsvCell(itm.Subject)
                n = n + 1
            End If

            Err.Clear
            On Error GoTo 0
        End If
    Next

    ts.Close
    ExportFolder = n
End Function


' Internal senders come back as X500/EX addresses. Fall back to the MAPI
' SMTP property so the address matches what the Sent folder shows.
Private Function SenderSmtp(itm As Object) As String
    Dim s As String
    On Error Resume Next

    s = itm.SenderEmailAddress

    If InStr(1, s, "/O=", vbTextCompare) > 0 Or InStr(s, "@") = 0 Then
        Dim alt As String
        alt = itm.PropertyAccessor.GetProperty( _
            "http://schemas.microsoft.com/mapi/proptag/0x39FE001E")
        If InStr(alt, "@") > 0 Then s = alt
    End If

    Err.Clear
    SenderSmtp = LCase$(s)
End Function


Private Function RecipientList(itm As Object) As String
    Dim r As Object, out As String, addr As String
    On Error Resume Next

    For Each r In itm.Recipients
        ' 1 = olTo. Cc and Bcc are deliberately excluded: being cc'd does not
        ' imply you were expected to reply.
        If r.Type = 1 Then
            addr = ""
            addr = r.Address
            If InStr(1, addr, "/O=", vbTextCompare) > 0 Or InStr(addr, "@") = 0 Then
                Dim alt As String
                alt = r.PropertyAccessor.GetProperty( _
                    "http://schemas.microsoft.com/mapi/proptag/0x39FE001E")
                If InStr(alt, "@") > 0 Then addr = alt
            End If
            If Len(addr) > 0 Then
                If Len(out) > 0 Then out = out & ";"
                out = out & LCase$(addr)
            End If
        End If
    Next

    Err.Clear
    RecipientList = out
End Function


Private Function CsvCell(v As Variant) As String
    Dim s As String
    s = CStr(Nz(v))
    s = Replace(s, vbCrLf, " ")
    s = Replace(s, vbCr, " ")
    s = Replace(s, vbLf, " ")
    s = Replace(s, """", """""")
    CsvCell = """" & s & """"
End Function


Private Function Nz(v As Variant) As String
    If IsNull(v) Or IsEmpty(v) Then
        Nz = ""
    Else
        Nz = v
    End If
End Function
