import { Component, input } from '@angular/core';

export type IconName =
  | 'layout-dashboard'
  | 'cpu'
  | 'plug'
  | 'circle-help'
  | 'external-link'
  | 'search'
  | 'plus'
  | 'refresh-cw'
  | 'arrow-left'
  | 'chevron-left'
  | 'chevron-right'
  | 'chevrons-left'
  | 'chevrons-right'
  | 'x'
  | 'pencil'
  | 'eye'
  | 'lock'
  | 'trash-2'
  | 'shield-check'
  | 'radio'
  | 'clock'
  | 'circle-check'
  | 'circle-alert'
  | 'menu'
  | 'database'
  | 'check'
  | 'loader-circle';

@Component({
  selector: 'app-icon',
  template: '',
  host: {
    'aria-hidden': 'true',
    '[style.mask-image]': 'url()',
    '[style.-webkit-mask-image]': 'url()',
  },
  styles: `
    :host {
      display: inline-block;
      width: 1.15em;
      height: 1.15em;
      flex-shrink: 0;
      background: currentColor;
      mask-size: contain;
      mask-repeat: no-repeat;
      mask-position: center;
      vertical-align: middle;
    }
  `,
})
export class Icon {
  readonly name = input.required<IconName>();
  protected url(): string {
    return `url("/icons/${this.name()}.svg")`;
  }
}
