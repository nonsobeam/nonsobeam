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
'   2. Developer tab > Visual Basic  (or Alt+F11).
'   3. Insert > Module.
'   4. Paste this whole file in.
'   5. Edit OUT_DIR below if your folder differs.
'   6. Run > Run Sub/UserForm  (or F5).
'
' It writes inbox.csv and sent.csv, then reports how many messages it
' exported and how many it scanned.

Option Explicit

' ---- settings -------------------------------------------------------------

Const OUT_DIR As String = "C:\Users\Thaddeus\sla\"
Const START_DATE As String = "2025-07-01"
Const END_DATE As String = "2026-06-30"

' ---------------------------------------------------------------------------

' Set by ExportFolder: how many mail items it walked past, regardless of
' whether they fell inside the date range. Reported so that a zero result
' distinguishes "found no mail at all" from "found mail, none in range".
Private LastSeen As Long

Public Sub ExportMailForSLA()
    Dim nInbox As Long, nSent As Long
    Dim seenInbox As Long, seenSent As Long

    On Error GoTo Fail

    nInbox = ExportFolder(olFolderInbox, OUT_DIR & "inbox.csv", True)
    seenInbox = LastSeen

    nSent = ExportFolder(olFolderSentMail, OUT_DIR & "sent.csv", False)
    seenSent = LastSeen

    MsgBox "Done." & vbCrLf & vbCrLf & _
           "inbox.csv:  " & nInbox & " exported  (" & seenInbox & " scanned)" & vbCrLf & _
           "sent.csv:   " & nSent & " exported  (" & seenSent & " scanned)" & vbCrLf & vbCrLf & _
           "Date range: " & START_DATE & " to " & END_DATE & vbCrLf & _
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
    Dim startD As Date, endD As Date
    Dim seen As Long

    startD = CDate(START_DATE)
    endD = CDate(END_DATE) + 1

    Set ns = Application.GetNamespace("MAPI")
    Set fld = ns.GetDefaultFolder(folderId)
    Set items = fld.items

    ' Try to narrow with Restrict first — much faster on a big folder. The date
    ' literal it needs is locale-dependent, so this quietly does nothing on some
    ' machines; the loop below re-checks every date anyway.
    If isInbox Then
        dateField = "[ReceivedTime]"
    Else
        dateField = "[SentOn]"
    End If

    filt = dateField & " >= '" & Format(startD, "ddddd h:nn AMPM") & "' AND " & _
           dateField & " <= '" & Format(endD, "ddddd h:nn AMPM") & "'"

    On Error Resume Next
    Dim narrowed As Object
    Set narrowed = items.Restrict(filt)
    If Err.Number = 0 Then
        ' A filter that matches nothing usually means the locale format was
        ' wrong rather than an empty mailbox. Fall back to the full folder.
        If narrowed.Count > 0 Then Set items = narrowed
    End If
    Err.Clear
    On Error GoTo 0

    Set fso = CreateObject("Scripting.FileSystemObject")
    Set ts = fso.CreateTextFile(outPath, True, True)   ' True = unicode

    ts.WriteLine "conversation_id,timestamp,sender,recipients,subject"

    For Each itm In items
        If TypeName(itm) = "MailItem" Then
            seen = seen + 1

            On Error Resume Next
            Err.Clear

            stamp = 0
            If isInbox Then
                stamp = itm.ReceivedTime
            Else
                stamp = itm.SentOn
                If stamp = 0 Then stamp = itm.CreationTime
            End If
            Err.Clear

            If stamp >= startD And stamp <= endD Then
                ts.WriteLine _
                    CsvCell(itm.ConversationID) & "," & _
                    CsvCell(Format(stamp, "yyyy-mm-dd hh:nn:ss")) & "," & _
                    CsvCell(SenderSmtp(itm)) & "," & _
                    CsvCell(RecipientList(itm)) & "," & _
                    CsvCell(itm.Subject)
                If Err.Number = 0 Then n = n + 1
            End If

            Err.Clear
            On Error GoTo 0
        End If
    Next

    ts.Close
    LastSeen = seen
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
    Dim alt As String
    On Error Resume Next

    For Each r In itm.Recipients
        ' 1 = olTo. Cc and Bcc are deliberately excluded: being cc'd does not
        ' imply you were expected to reply.
        If r.Type = 1 Then
            addr = ""
            addr = r.Address
            If InStr(1, addr, "/O=", vbTextCompare) > 0 Or InStr(addr, "@") = 0 Then
                alt = ""
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
