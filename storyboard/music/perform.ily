\version "2.24.0"
%% Penguin's Storyboard: what the score says about HOW to play, written out for the sound engine.
%%
%% LilyPond's MIDI file gives every note one loudness and nothing else, so a held chord under a
%% crescendo stays flat. This file is included in front of every score the app engraves. It adds a
%% small listener to each staff that writes the dynamics, hairpins and accents, with their exact
%% positions, to perform.txt next to the output. The app reads that file and plays them for real.
%% It changes nothing about how the score looks. Lines it writes:
%%   S n kind group                 a staff (n counts from 0 in the order the staves are made)
%%   I n "instrument name" "midiInstrument"
%%   N n tick pitch                 one of the staff's notes (tick: 1536 to the whole note; middle C is 60)
%%   A n tick name                  an absolute dynamic: pp, mf, sfz, fp, ...
%%   H n tick direction start       a hairpin: direction 1 louder, -1 softer; start 1 where it begins, 0 where it ends
%%   X n tick name                  an articulation: accent, marcato, staccato, tenuto, ...
%%   T n tick type                  a tremolo or roll on the notes at that tick (c1:32 gives type 32)

#(define sb-port #f)
#(define sb-count 0)
#(define sb-groups (make-hash-table))
#(define sb-group-count 0)

#(define (sb-out . parts)
   (if (not sb-port) (set! sb-port (open-output-file "perform.txt")))
   (for-each (lambda (x) (display x sb-port) (display " " sb-port)) parts)
   (newline sb-port))

#(define (sb-tick context)
   (let ((m (ly:context-current-moment context)))
     (inexact->exact (round (* 1536 (ly:moment-main m))))))

#(define (sb-text x)
   ;; a property that may be a string, a markup or nothing, as one quoted line of plain text
   (let* ((s (cond ((string? x) x)
                   ((markup? x) (markup->string x))
                   (else "")))
          (clean (string-map (lambda (c) (if (or (char=? c #\") (char=? c #\newline) (char=? c #\\)) #\space c)) s)))
     (string-append "\"" clean "\"")))

#(define (sb-group-of context)
   ;; staves braced together as one keyboard instrument share their dynamics
   (let ((parent (ly:context-parent context)))
     (if (and (ly:context? parent) (memq (ly:context-name parent) '(PianoStaff GrandStaff)))
         (or (hashq-ref sb-groups parent)
             (begin (set! sb-group-count (+ sb-group-count 1))
                    (hashq-set! sb-groups parent sb-group-count)
                    sb-group-count))
         0)))

#(define (sb-listener context)
   (let ((n sb-count) (notes 0))
     (set! sb-count (+ sb-count 1))
     (sb-out "S" n (ly:context-name context) (sb-group-of context))
     (make-engraver
      (listeners
       ((note-event engraver event)
        (let ((p (ly:event-property event 'pitch)))
          (if (and (ly:pitch? p) (< notes 600)
                   (zero? (ly:moment-grace (ly:context-current-moment context))))
              (begin (set! notes (+ notes 1))
                     (sb-out "N" n (sb-tick context) (+ 60 (ly:pitch-semitones p)))))))
       ((absolute-dynamic-event engraver event)
        (let ((t (ly:event-property event 'text)))
          (if (string? t) (sb-out "A" n (sb-tick context) t))))
       ((span-dynamic-event engraver event)
        (sb-out "H" n (sb-tick context)
                (if (memq 'decrescendo-event (ly:event-property event 'class)) -1 1)
                (if (eqv? (ly:event-property event 'span-direction) START) 1 0)))
       ((articulation-event engraver event)
        (let ((a (ly:event-property event 'articulation-type)))
          (if (or (symbol? a) (string? a)) (sb-out "X" n (sb-tick context) a))))
       ((tremolo-event engraver event)
        (let ((k (ly:event-property event 'tremolo-type)))
          (if (integer? k) (sb-out "T" n (sb-tick context) k)))))
      ((finalize engraver)
       (sb-out "I" n (sb-text (ly:context-property context 'instrumentName))
               (sb-text (ly:context-property context 'midiInstrument)))
       (if sb-port (force-output sb-port))))))

\layout {
  \context { \Staff \consists #sb-listener }
  \context { \DrumStaff \consists #sb-listener }
  \context { \RhythmicStaff \consists #sb-listener }
  \context { \TabStaff \consists #sb-listener }
  \context { \Dynamics \consists #sb-listener }
}
