export interface OutgoingEmail {
  recipient: string
  subject: string
  body: string
}

export interface EmailSender {
  send(email: OutgoingEmail): Promise<{ providerMessageId: string }>
}

/** Sends nothing. Returns a fake message id and logs the email. */
export class MockEmailSender implements EmailSender {
  readonly sent: OutgoingEmail[] = []
  private counter = 0

  async send(email: OutgoingEmail) {
    this.sent.push(email)
    this.counter += 1
    if (import.meta.env?.DEV) console.info('[MockEmailSender] would send', email)
    return { providerMessageId: `mock-msg-${Date.now()}-${this.counter}` }
  }
}
